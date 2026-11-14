# 2.6 deploy-day runbook: 2.4.2-beta.7 → 2.4.2-beta.8

This runbook orders the 2.6 deploy day for the Engineering add-on: the
checks before Ready, merge and publication, the update from 2.4.2-beta.7 to
2.4.2-beta.8, the smoke tests, sequence 18 signing, live acceptance and the
restore-first recovery. It follows the
[release notes](../V2_4_2_BETA8_RELEASE_NOTES.md) and the
[acceptance contract](../V2_4_2_BETA8_ACCEPTANCE.md).

**This runbook is not an authorization.** Each **[JOSH APPROVAL]** step
needs Josh's explicit approval at that moment. A runbook line is not
approval.

- Written against the HAMCP-332 release head `41786cc0` (branch
  `agent/HAMCP-332-release-2.6.0`). File:line citations without a commit
  are at that head; citations marked "at the tag" are at `v2.4.2-beta.7`
  (`b614be43`).
- Labels: **FACT** comes with a file:line, commit or receipt. **INFERENCE**
  is reasoning.
- Rulings are cited by date and label. Release-gate rulings with a label
  only (for example "23:01 ruling 5" or "D16") are recorded in the HAMCP-332
  build record and were all made by 2026-11-11. Architect rulings A to H
  were relayed by Josh on 2026-11-12; they are architect rulings, not
  Josh's own. Josh's decisions 3 (restore-first recovery) and 4 (the A2
  restore rehearsal) date from 2026-11-12. Rulings R-a to R-g are Josh's,
  of 2026-11-13 (signing plan §9D). The 17:05 ruling 2 (architect,
  relayed by Josh 2026-11-13, finding HAMCP332-RUNBOOK-SIGNING-COMMIT) sets
  the two commits used in 1.2-1.4, 7.1, 7.2 and 7.5-7.6; it is not Josh's
  own. The 19:05 record item 1 (architect, relayed by Josh 2026-11-13,
  after the beta.8 deploy) sets the S7 row, the Phase 5 known blocker and
  the 6.2 deviation note; it is not Josh's own either.
- "Check A1" and "check A2" are the two checks inside architect ruling A.
  They are not the earlier rulings cited below as A2, A3 or A8, nor the
  23:11 rulings A1 and A2.
- Values that exist only after the release pull request merges (the merge
  SHA, the publication run, the image digest, the catalog hash and the
  recomputed fingerprints on the merge commit) are not in this runbook.
  Steps 1.2 to 1.5 compute them on the merge commit.
- Roles:
  - **Josh**: Home Assistant UI, Ready, signing, approvals.
  - **Architect**: read-only MCP checks and the recompute.
  - **Orchestrator**: GitHub, receipts and records.

Times in CST.

---

## Hard stops (apply to every step)

- Stop on the first unexpected status. Record it and tell Josh. Rollback is
  Josh's decision.
- Never retry an uncertain write with a new `operation_id`. Read
  `get_execution_task` instead, no more often than every 10 s, and stop after
  a few minutes (23:01 ruling 5).
- Never remove lock records, roll back the registry journal, or delete a
  registry cache (22:23 ruling 1; ruling A8).
- Never paste the webhook id or connector URL anywhere (23:24 ruling).
- Keep the security metadata output out of shared documents (acceptance
  §7.3).
- Every 2.6 backup (the pre-deploy full backup 2.6 and the A2 rehearsal
  backup 0.4) is made in the Home Assistant UI, never through
  `create_backup_plan` (architect ruling on HAMCP-381, relayed by Josh
  2026-11-12). INFERENCE for the reason: a backup plan is a plan-governed
  task with a 24-hour evidence window, the family the HAMCP-381 limitation
  applies to.

## Expected count drop after a restart (retention)

After any restart, restore or deploy, expect the governance plan and task
counts to drop by the number of terminal records whose `updated_at` is older
than 90 days. This is not a fault (architect, A2 result, 2026-11-13).
- FACT: at startup only, the governance service deletes terminal plan and
  task records older than the retention period (default 90 days):
  `governance/service.py:885-887` (`:873-875` at the tag);
  `governance/storage.py:1468-1470` and `governance/task_storage.py:903-905`.
- When a snapshot comparison shows lower plan or task totals after a
  restart, check that only terminal records went, and that no pending,
  approved or non-terminal record is missing. A missing non-terminal record
  is a fault: stop and report.
- A2 saw plans 520 -> 514 and tasks 344 -> 342 after a start 4.2 days after
  the previous one.

## Idle check: no plan or task in flight (HAMCP-381 ruling)

Architect ruling relayed by Josh, 2026-11-12: "Runbook precondition for
deploy, tier-4 restore and the A2 rehearsal: no non-terminal plan or family
task (list_change_plans, list_execution_tasks, plus a per-family check if
list_execution_tasks does not cover the families)." Josh's decision 3a
narrows it: "The precondition "no non-terminal plan or family task" stays
for deploy and for A2. It is not needed before a full restore." It applies
at 0.4 step 1 (A2) and 2.3 (deploy), on whichever version is running.
Before the Engineering-only restore (RB5) it is kept as INFERENCE, not
ruled: the ruling named the tier-4 restore, which was then that partial
restore, and decision 3a lifts it only for the full restore.

All calls are reads. Use a fresh client session.

1. `list_change_plans`: no pending plan and no approved plan that is not
   applied.
2. `list_execution_tasks`, once per non-terminal state, each with
   `limit` 100: `created`, `preflight`, `dispatching`, `observing`,
   `verifying`. Every call returns `count` 0.
   - FACT: the default `limit` is 20, the list is newest first, and the
     limit is applied after the state filter
     (`governance/service.py:2601-2639`, `:2589-2627` at the tag;
     `governance/task_storage.py:802-804`, the same lines at the tag). An
     unfiltered default call can miss an old non-terminal task behind 20
     newer ones.
3. Per-family check. FACT: `list_execution_tasks` does NOT cover the typed
   families. It reads only the governance task store
   (`execution-tasks-v1`, `governance/task_storage.py:32`); fan, power,
   helper-value and (2.4.2-beta.8 only) device-configuration tasks are in
   separate stores (`ordinary-fan-v1`, `ordinary-power-v1`,
   `ordinary-helper-value-v1`, `ordinary-device-configuration-v1`). So also
   read `get_server_health` and check, in each of `ordinary_fan`,
   `ordinary_power`, `ordinary_helper_value` and, on 2.4.2-beta.8,
   `ordinary_device_configuration`:
   - `nonterminal_tasks` is 0;
   - `active_executions` is 0;
   - `conflict_hold_count` is 0;
   - `retained_lock_count` is 0.
   - FACT: these are counts produced by each family's `health()`
     (`health.py:119-122`, `:103-105` at the tag; `fan/service.py:970-982`,
     `:892-904` at the tag). They never name a task.
   - Not established in source or by observation: whether the
     client-visible `get_server_health` output passes these fields through
     unfiltered and untrimmed. If a field is missing from the output, the
     check is not met: stop and tell Josh.
   - 2.4.2-beta.7 has no `ordinary_device_configuration` key and no
     device-configuration tool (FACT: `health.py:103-105` at the tag).
4. Holds: `get_server_health` shows no hold (as in 2.3).

If a family count is not 0:
- The task id is in the family tool's own response and in the audit log
  (`get_audit_log`; at most 500 rows of the current file, not the rotated
  one). The deployed audit path and whether auditing is on are not checked
  here.
- On 2.4.2-beta.7, reading that task with `get_execution_task` is NOT a pure
  read: for a family task it can claim, append events and change locks
  (`tools/governance.py:544-557` and `fan/service.py:808-845`, both at the
  tag). Keep to the polling rule: no more often than every 10 s, stop after
  a few minutes.
- On 2.4.2-beta.8 it is a pure read (HAMCP-379, #436): the family's
  `read_task` returns the stored record and receipt and changes nothing
  (`fan/service.py:814-820`; `tools/governance.py:545-563`). Recovery runs
  in the 30 s sweep (`recover_once`, `fan/service.py:876`).
- Do not deploy, rehearse or run the Engineering-only restore until the
  counts are 0 or Josh decides.

---

## Phase 0: before Ready

**0.1 Pre-release pull requests merged**
- Who: orchestrator.
- Action: confirm these are in the release head, in order:
  1. HAMCP-364 (#435, merge `a9144591`);
  2. HAMCP-379 (#436, merge `1d4607a5`);
  3. HAMCP-377 (#437, merge `7d2989ad`);
  4. #395, the signing plan, folded into the release head by `3bd5fe3f`,
     with sheet A refreshed by `41786cc0`.
- Check: the release pull request head contains all four. The CI rollback
  image is 2.4.2-beta.7
  `sha256:5801d42fd19bfe813cccfe0a81b218d9f3a8cabf6f12ee044a160c0603c83c47`
  (`.github/workflows/ci.yml:989-990`; rulings 31-32).
- Stop: any missing → no Ready.

**0.2 Release pull request evidence**
- Who: orchestrator.
- Action: on the exact release pull request head, confirm:
  - the pull request's CI is green;
  - the 8.5.0 add-on runtime acceptance jobs are green;
  - the exact-image read-gateway lane is green (ruling C14);
  - Evidence is green;
  - the release notes and acceptance contain no "Pending".
- Check: each run is for that head SHA. Record each run.
- Stop: any red or a different SHA → no Ready. Josh's rule of 2026-11-11:
  no merge before the pull request's own CI is green.

**0.3 HAMCP-379 and sequence 18 scope**
- Who: orchestrator.
- Check:
  - HAMCP-379 is fixed in this release (#436, merge `1d4607a5`). The
    release notes describe the fix under "Execution-task recovery
    (HAMCP-379), fixed in this release".
  - `core.device_configuration_entity_operation` is in sequence 18 with the
    other five profiles: six profiles (HAMCP-379 design ruling 7, relayed by
    Josh 2026-11-12: "Seq 18: all six profiles as planned (ruling C
    answered: device configuration is covered)."; R-a). The reference counts
    in 1.3, Phase 7, 8.1 and RB2 are written for six.
- Stop: the HAMCP-379 merge is missing → no Ready.

**0.4 A2 rehearsal: PASSED 2026-11-13** [JOSH APPROVAL]
- Who:
  - Josh performs the partial backup and the restore, Engineering beta
    add-on only, in the Home Assistant UI.
  - The architect takes read-only snapshots before and after and judges
    pass or fail.
  - No agent touches the add-on (Josh's decision 4; architect ruling A,
    check A2).
- Status: the rehearsal goes ahead before Ready (Josh's decision 4).
- **Result: PASSED, 2026-11-13** (architect's judgment, relayed by Josh).
  Josh performed the backup and restore remotely in the Home Assistant UI;
  the architect took the snapshots through Engineering, read-only.
  - Partial backup of only the Engineering add-on (2.4.2-beta.7), restored
    at about 11:45 CST. Before-snapshot 17:41 UTC (11:41 CST); after-snapshot
    17:45-17:46 UTC (11:45-11:46 CST).
  - Identical after restore: version 2.4.2-beta.7; Core 2026.11.0
    connected; 110 tools (82 + 28); ha-mcp 8.5.0 admitted exact; registry
    seq 17 current with the same withheld capability
    (`core.marketplace_read`); 0 active plans, 0 non-terminal tasks, 0 locks
    or holds; f3 ready; 2 acknowledged manual-review tasks; 7 trace watches
    with unchanged counts, polling resumed; typed-control receipts (fan 10,
    power 54, helper 2).
  - Audit continuity: the before-snapshot entries at 17:41:42 and 17:41:46
    UTC are present, followed by the startup reconcile at 17:45:05 UTC. This
    proves the restore used the fresh partial backup.
  - Explained difference: plans 520 -> 514 and tasks 344 -> 342, from the
    startup retention cleanup (see "Expected count drop after a restart").
    The previous start was 4.2 days earlier. The mechanism is verified in
    code; the identity of the 8 removed records was not checked.
  - Not proven by A2: restoring a 2.4.2-beta.7 add-on backup over an
    installed 2.4.2-beta.8, which is the actual downgrade. A2 restored the
    same version. It also does not exercise the full restore (RB4).
  - Observation: `approval_notifications` `addon_identity_status` was
    `unresolved` after the restart (it was `verified` before). The
    architect rechecks it before deploy (2.2).
- Action, as run on 2.4.2-beta.7 before Ready:
  1. Architect, snapshot before: `server_info`, `list_change_plans`,
     `list_execution_tasks`, `get_server_health`. Go on only with no plan
     or task in flight: no pending or approved plan, no non-terminal task,
     no hold. Run the full Idle check above, including the per-family
     counts in `get_server_health`; `list_execution_tasks` alone does not
     show family tasks (HAMCP-381 ruling).
  2. Orchestrator: repeat check A1 (0.5) first. It is a separate read-only
     registry check, not part of the restore.
  3. Josh: take a partial backup of only the Engineering beta add-on in the
     Home Assistant UI (Settings > System > Backups), not through
     `create_backup_plan` (HAMCP-381 ruling).
  4. Josh: restore it, Engineering beta add-on only. Take the exact UI
     labels for a partial backup and a partial restore from the Home
     Assistant UI at the time.
  5. Wait until the add-on has started and its log shows startup readiness
     complete.
  6. Architect, snapshot after: the same four calls, from a fresh client
     session.
- Check, against the snapshot before:
  - `server_info`: version 2.4.2-beta.7, the same `build_sha`,
    `build_dirty` false, Home Assistant connected;
  - `list_change_plans`: the same records, none pending or approved, less
    any terminal records removed by retention (see "Expected count drop
    after a restart");
  - `list_execution_tasks`: the same records, none non-terminal, less any
    terminal records removed by retention;
  - `get_server_health`: seq 17 with 32 references, no hold, the same
    upstream version and fingerprint.
- Stop: the restore fails, the add-on does not start, or the architect
  judges a snapshot different → stop, record it and tell Josh. Tier 4 stays
  labelled untested (0.6).
- INFERENCE: this rehearses the secondary path (RB5), an Engineering-only
  restore of the same version. It does not exercise the full restore of
  tier 4 (RB4), and it does not show a restore of 2.4.2-beta.7 over an
  installed 2.4.2-beta.8 while the repository advertises 2.4.2-beta.8.

**0.5 Check A1: the 2.4.2-beta.7 tag resolves to the pinned digest**
- Who: orchestrator, on scriptbox. Read-only, anonymous registry read.
- Action: run the command below and record the command and its output
  (architect ruling A, check A1).

  `docker buildx imagetools inspect ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.7`
- Check:
  - FACT: run 2026-11-12 00:13:08 CST. The tag resolved to the OCI image
    index
    `sha256:5801d42fd19bfe813cccfe0a81b218d9f3a8cabf6f12ee044a160c0603c83c47`,
    with manifests linux/amd64 `sha256:8a60a87c…814f` and linux/arm64
    `sha256:55676da8…7b24`.
  - That is the digest approved for the CI rollback image (ruling 32) and
    pinned at `.github/workflows/ci.yml:989`.
  - A tag is mutable. Repeat the check immediately before Ready (1.1) and
    again immediately before any restore (0.4, RB4, RB5). Record each run.
  - INFERENCE: A1 shows what the registry serves for the tag. It does not
    show that Supervisor restores and starts it; that is check A2 (0.4).
- Stop: the tag resolves to any other digest → no Ready and no restore.
  Report it.

**0.6 Recovery method stated (restore-first)**
- Who: orchestrator.
- Action: confirm that the release notes' Rollback section and the rollback
  decision points below state the method of Josh's decision 3: "Recovery
  from a bad beta.8 is a restore from a full Home Assistant backup taken
  immediately before deploy. In-place rollback to beta.7 … is no longer a
  supported path."
- Check, each present:
  - Pre-deploy: a fresh full backup in the Home Assistant UI immediately
    before deploy (2.6). Not `create_backup_plan`, and not the nightly
    backup. It replaces the planned partial add-on backup.
  - Primary recovery, tier 4 (RB4): restore that full backup. It also
    reverts the Home Assistant objects created during acceptance. Stated
    as untested.
  - Secondary (RB5): a partial restore of only the Engineering add-on, when
    Home Assistant changes made after deploy must be kept. Architect
    ruling B's orphan step applies only to this path.
  - Before either restore: export the 2.4.2-beta.8 audit (`get_audit_log`).
    It is lost on restore.
  - In-place downgrade is unsupported, for the two 2.4.2-beta.7 findings in
    the rollback section below. They are reasons, not a recovery procedure.
  - Seq 18 signing is outside the Home Assistant backup. After a restore,
    2.4.2-beta.7 runs against seq 18 (decision 3b; 7.1-7.4).
  - FACT: `docs/BETA_DEPLOYMENT.md:370-373` documents roll-forward only ("a
    new beta release … newer than the failed release"); `:392-393` says
    "Do not downgrade metadata in place".
  - Architect ruling A: Supervisor reinstalls the backed-up add-on version
    and pulls its image by tag. INFERENCE: the same holds when the add-on
    comes from a full backup, so check A1 (0.5) applies to both restore
    paths.
- Stop: any item missing → no Ready. The A2 result is recorded in 0.4
  (passed 2026-11-13), with what it did and did not rehearse (Josh's
  decision 4). Whether the untested label on the full restore lifts with an
  A2 pass is not ruled (see 0.4 INFERENCE).

---

## Phase 1: merge and publish

**1.1 Ready** [JOSH APPROVAL: Ready]
- Who: Josh.
- Action: mark the release pull request (HAMCP-332) Ready for review.
  Immediately before, the orchestrator repeats check A1 (0.5) and records
  it.
- Check: the protected-base workflow waits for `validate`, then merges once.
  It publishes the version image, commit image, annotated tag
  `v2.4.2-beta.8` and the GitHub Release. FACT: `AGENTS.md`, Default
  Workflow.
- Stop: any head change after Ready withdraws it. Ready does not authorize
  deploy.

**1.2 Record the merge**
- Who: orchestrator.
- Action: record the merge SHA, the image digest and the tag target.
- Two commits (17:05 ruling 2a; Josh's decision 1d, 2026-11-13: fix G5 in
  2.6 before signing):
  - the release merge SHA is the **publish and deploy commit**;
  - the **signing commit** is the merge commit of the HAMCP-297 G5 pull
    request (pending: it exists only after that pull request merges).
    Every included lane re-runs there under R8 (signing plan §5A.4).
- Check: tag = merge SHA; publication run green; Release present.
- Stop: publication red → no deploy.

**1.3 Recompute on the release merge, then on the signing commit**
- Who: architect.
- Action: recompute all 38 profile fingerprints twice (ruling R-d,
  2026-11-13, signing plan §9D; rulings B11 and A8; 17:05 ruling 2b):
  - on the release merge SHA, for deploy;
  - again on the signing commit (1.2), before signing.
- Check: they match sheet A of the signing plan
  ([`HAMCP_332_2_6_0_SIGNING_PLAN.md`](../HAMCP_332_2_6_0_SIGNING_PLAN.md),
  "5A.15 Expected-values sheet (for the dry-run comparison)", part
  "A. Profiles added"). The six additions are 33-38, in R-b's order
  (signing plan §5A.5):
  - `core.marketplace_read` `9393b598…cfeb`;
  - `core.security_metadata_read` `9aa788b5…9c55`;
  - `core.device_configuration_entity_operation` `74a03a64…293c`;
  - `core.scene_create_delete_operation` `93378ae0…400d`;
  - `core.created_helper_delete_operation` `3f2e6813…cdbf4`;
  - `core.thread_matter_topology_read` `d03f005c…bb6c`.
  The 38 fingerprints must be unchanged between the release merge and the
  signing commit (17:05 ruling 2b).
- Stop: any mismatch → no signing. Deploy may still proceed (INFERENCE:
  the profiles are dormant until seq 18 is signed).

**1.4 Catalog hashes on the release merge, then on the signing commit**
- Who: orchestrator arranges a read-only side session.
- Action: re-run the catalog hashes on the release merge SHA, for deploy
  (ruling B11), and again on the signing commit (1.2), before signing
  (17:05 ruling 2b).
- Check: 86 Engineering tools, with the 4 added and 6 changed tools
  declared in the release notes' Tool catalog section; the catalog hash
  equals the release pull request head's. The catalog must be unchanged
  between the release merge and the signing commit.
- Stop: mismatch → stop and report. A change between the two commits → no
  signing.

**1.5 Regenerate the changing facts on the merge commit**
- Who: orchestrator arranges a read-only side session. It runs alongside
  the architect's 38-fingerprint recompute in 1.3.
- Action: on the release pull request's merge commit, regenerate every
  value this runbook, the release notes and the acceptance contract carry
  as read at the release head (architect ruling H, relayed by Josh
  2026-11-12):
  - counts: tools and references. Live-only counts (28 delegated, 114
    total) stay marked as coming from the baseline and S2/S5;
  - file:line citations;
  - receipt references: path and sha256 for 12a, 12b and 12c
    (`docs/evidence/hamcp377/lane/`);
  - the rollback digest: the 2.4.2-beta.7 image digest in `ci.yml`,
    compared with the latest check A1 (0.5).
- Check: every such value is confirmed unchanged or replaced by the
  regenerated one, with the merge SHA recorded beside it.
- Stop: a regenerated count, receipt or digest differs from what the merged
  release notes and acceptance say → stop and report. No signing.

---

## Phase 2: pre-deploy state

The pre-deploy backup is a full backup and the last step of this phase,
2.6, immediately before deploy (Josh's decision 3a). It replaces the
planned partial add-on backup that was step 2.1; 2.1 is no longer used.

**2.2 Baseline capture (read-only)**
- Who: architect.
- Action: from a fresh client session, capture:
  - `server_info`;
  - `list_capabilities`;
  - `get_server_health`;
  - `list_change_plans`;
  - `list_execution_tasks`.
- Also recheck `approval_notifications` `addon_identity_status` in
  `get_server_health`: it was `unresolved` after the A2 restart (0.4).
- Check: record these for the smoke tests (D16):
  - version 2.4.2-beta.7;
  - tool count 110;
  - the 28 delegated names;
  - upstream 8.5.0 and its fingerprint;
  - seq 17 and 32 references;
  - holds;
  - the values the acceptance contract takes from the baseline (acceptance
    §4: the health key names, the catalog fingerprint, the scene
    `config_id` form, the helper arguments and the Eve entity ids).
- Stop: see 2.3.

**2.3 Baseline gate**
- Who: architect.
- Action: confirm there is no pending or approved-not-applied plan, no
  non-terminal task, and no hold (ruling B10).
- Check: all three lists are empty of those, and the Idle check above
  passes in full, including the per-family counts (HAMCP-381 ruling:
  precondition for deploy).
- Stop: anything found → resolve it first, with Josh's approval if it
  mutates. Then repeat 2.2.

**2.4 Record `ha_timeout_seconds`**
- Who: Josh reads it; the architect records it.
- Action: read the add-on's Configuration tab (23:01 ruling 6).
- Check:
  - FACT: an option with default 60
    (`hass_mcp_engineering_beta/config.yaml:55`). Its schema is `float` with
    no range (`:94`).
  - Architect ruling F (relayed by Josh 2026-11-12): record only; no change
    in 2.6. The HAMCP-379 containment that ships in this release does not
    depend on the timeout.
- Stop: none. This is a record only. No change in 2.6.

**2.5 Optional webhook rotation** [JOSH APPROVAL]
- Who: Josh only.
- Action: Josh decides whether to rotate (23:24 ruling; optional at this
  deploy).
- Check: nobody writes the id or URL anywhere.
- Stop: skip if undecided.

**2.6 Full backup, immediately before deploy** [JOSH APPROVAL]
- Who: Josh.
- Action: take a fresh full backup in the Home Assistant UI (Settings >
  System > Backups) immediately before 3.1. Not `create_backup_plan`, and
  not the nightly backup (Josh's decision 3a; HAMCP-381 ruling, Hard
  stops).
- Check:
  - the new backup is listed with today's time, taken after 2.3 passed;
  - nothing else is changed between this backup and 3.1.
  - Not checked here: whether the backup list shows the Engineering add-on
    version inside a full backup.
  - INFERENCE: this one backup serves both restore paths. RB4 restores all
    of it; RB5 restores only the Engineering add-on from it. Whether the
    Home Assistant UI offers an add-on-only restore from a full backup is
    not checked here.
- Stop: the backup fails → no deploy.

---

## Phase 3: deploy (profiles dormant)

**3.1 Update the add-on** [JOSH APPROVAL]
- Who: Josh.
- Action: Settings → Add-ons → Engineering → Update to 2.4.2-beta.8.
- Check: the add-on starts, and its log shows startup readiness complete
  (HAMCP-350).
- Stop: it fails to start → rollback point RB1.

---

## Phase 4: smoke tests S1-S12 (read-only, except the S12 trace watch)

- Who: architect, from a new client session.
- **[JOSH APPROVAL]**: Josh's single smoke-test approval covers the whole
  phase, the S12 trace watch included (architect ruling G, relayed by Josh
  2026-11-12).
- Source: acceptance §4, as accepted by D16.
- Compare each check with the 2.2 baseline.

| # | Check | Expect |
| --- | --- | --- |
| S1 | `server_info` | version 2.4.2-beta.8, `build_sha` = merge SHA, `build_dirty` false, `schema_version` `"1"`, Home Assistant connected on Core 2026.11.0 |
| S2 | counts | `engineering_registered_tool_count` 86, `canonical_tool_count` 25, `dynamic_upstream_tool_count` 28, `tool_count` 114 |
| S3 | `get_server_health` | upstream 8.5.0 with no unadmitted flags; `ordinary_device_configuration` block present, no hold or task |
| S4 | registry | seq 17, 32 references, 0 mismatches |
| S5 | `tools/list` | 114 tools; `ha_get_hacs_info` absent; four new tools present |
| S6 | `list_capabilities` | answers; record it beside the baseline |
| S7 | add-on log: `startup_phase_timing` events, not `get_server_health` (19:05 record item 1) | the HAMCP-350 startup phase timings are present, with `readiness_deadline_elapsed` false |
| S8 | upstream fingerprint | `upstream_catalog_fingerprint` equals baseline |
| S9 | one delegated read | `ha_get_state` on a harmless sensor attributed to ha-mcp, no fallback |
| S10 | one native read | `provider: engineering`, `fallback_occurred: false` |
| S11 (reads) | marketplace, security metadata, topology | each `pending_signed_registry_admission`, nothing sent |
| S12 | `get_logbook` with window and filter; batch analysis on 2-3 automations; `dashboard_integrity_analysis` on one dashboard | bounded results |
| S12 (trace watch) | `set_automation_trace_watch` on one automation: `add`, `list`, `remove`, `list` | the watch is listed after `add` and absent after `remove` |

The S12 trace watch is a write to the Engineering store only, not to the
house (architect ruling G). It is folded into Josh's single smoke-test
approval: add, read back with `list`, remove, read back with `list`. There
is no separate ceremony. FACT: the tool's actions are `add`, `remove`,
`list` and `renew` (`tools/trace_snapshots.py:106-107`). Record which
automation was watched.

- Stop: any miss → record it and tell Josh → RB1.
- PASS needs every row.

---

## Phase 5: refresh clients

**5.1 Fresh sessions**
- Who: Josh.
- Action:
  - Claude app or Android: start a new chat. Claude Code: start a new
    session. Codex: restart.
  - Reconnect the connector only if the four new tools are absent (D18).
- Check: the new chat lists `control_device_configuration`,
  `get_security_metadata`, `get_thread_matter_topology` and
  `automation_reliability_batch_analysis`.
- Stop: still missing after reconnect → stop and report.
- Known blocker C1 (19:05 record item 1): on the 2.6 deploy day, a
  brand-new Claude chat could not load the connector. An existing chat saw
  the four new tools after an app restart. Record Phase 5 as blocked by
  C1, not failed, when only a brand-new Claude chat fails to load the
  connector. C1 (legacy-only MCP protocol) is 2.7 intake.

---

## Phase 6: write-tool refusal probes (dormant)

Each probe is separately approved, on a harmless target, with readback
(D17; acceptance §5).

The split of the probes is accepted: 6.1 and 6.2 here, before signing, and
9.7 after activation (architect ruling E, relayed by Josh 2026-11-12). Its
condition: a probe leaves no pending or approved plan. Read back
`list_change_plans` at each checkpoint: after 6.1, after 6.2 and after 9.7.
Poll `get_execution_task` no more often than every 10 s.

**6.1 Scene-create probe** [JOSH APPROVAL]
- Who: architect.
- Action: call `create_scene_configuration_plan(action="create", …)`.
- Check: the call refuses with `pending_signed_registry_admission`, and
  `list_change_plans` shows no new plan.
- Stop: a plan is created → stop and tell Josh.

**6.2 Device-configuration probe** [JOSH APPROVAL]
- Who: architect.
- Action: call `control_device_configuration` on an Eve plug config entity
  (master bedroom TV or kitchen).
- Check:
  - the call refuses with `device_configuration_core_authority_unavailable`;
  - the entity state is unchanged;
  - `get_audit_log` shows no dispatch;
  - `list_change_plans` shows no pending or approved plan (architect ruling
    E).
- Stop: anything was dispatched, or a pending or approved plan is left →
  stop and tell Josh.
- Deviation on the 2.6 deploy day (19:05 record item 1): both named Eve
  plugs were offline (F063). The probe used the offline master bedroom TV
  select with `"previous"` instead, on the architect's ruling with Josh's
  approval. It refused with `device_configuration_core_authority_unavailable`,
  with no dispatch and no plan. A different target needs the same ruling
  and approval.

---

## Phase 7: seq 18 candidate and signing

Seq 18 signing is outside the Home Assistant backup; after a restore,
2.4.2-beta.7 runs against seq 18. So 7.1-7.4 stay: the 12a receipt, the
same candidate tool, 2.4.2-beta.7's closed reference field set and the
signed order of `previous_capabilities` (Josh's decision 3b). Revocation
and HAMCP-367 stay the only undo for seq 18. The signing procedure itself
is the signing plan's (§5A.8).

**7.1 Rollback receipt 12a**
- Who: orchestrator.
- Action: cite the committed HAMCP-377 12a receipt:
  `docs/evidence/hamcp377/lane/12a-source.json`, sha256
  `b7b66591d2620e09063627c4dec6d2aa42ef94846b9f2f1526ce354d78646437`
  (writer 2.4.2-beta.8 at `aeb3b314`, reader 2.4.2-beta.7 at `b614be43`;
  see the [lane README](../evidence/hamcp377/lane/README.md)). Confirm its
  sha256 is unchanged on the merge commit (1.5).
- That sha256 holds only until `compare-candidate --record` is used (7.2).
  After recording, cite the new sha256 of `12a-source.json` from the
  receipt pull request instead (17:05 ruling 2c). Until then the new value
  is pending.
- Check: PASS. 2.4.2-beta.7 admits 33 of 38 references, including
  `core.marketplace_read`, with 5 not admitted and 0 quarantined (ruling
  29).
- Stop: missing or different → no signing (C12).

**7.2 Same candidate tool**
- Who: orchestrator.
- Action: once the real seq 18 candidate exists, compare its reference
  objects with those 2.4.2-beta.7 read in 12a (23:11 ruling A1):

  `python scripts/rollback_proof_lane.py compare-candidate docs/evidence/hamcp377/lane <core-candidate.json>`

  It compares the canonical bytes of each reference object, in order. With
  `--record` it also writes the result into the committed 12a receipt
  (17:05 ruling 2d):
  - run `--record` only after the real seq 18 candidate exists;
  - run it on a dedicated evidence branch with its own pull request,
    under register id HAMCP-377. Proposed branch pattern (a proposal, not
    yet ruled): `agent/HAMCP-377-seq18-record-<short-sha>`;
  - then cite the new 12a sha256 from that pull request (7.1).
- Check: identical. The 12a entry was built by
  `scripts/prepare_core_release_registry.py extend-capabilities`, the route
  the real candidate takes (lane README).
- Stop: any byte differs → no signing.

**7.3 Closed field set**
- Who: architect.
- Action: check every seq 18 reference uses exactly 2.4.2-beta.7's closed
  field set (`ha_core_readmission/registry_models.py` at the tag) (23:11
  ruling A2).
- Check: no extra or missing field.
- Stop: an unknown field → no signing. FACT, per 23:11 ruling A2:
  2.4.2-beta.7 rejects the whole entry, then falls back to cached seq 17 or
  loses Core-native capabilities.

**7.4 Order**
- Who: architect.
- Action: check `previous_capabilities` uses the signed order (R-b,
  signing plan §5A.5):
  - the 32 signed seq 17 references in their signed order;
    `core.typed_helper_value_operation` is signed 32nd (compiled 20th);
  - then the additions as 33-38, in compiled order (ruling A3; R-b).
- Check: matches.
- Stop: wrong order → no signing.

**7.5 Lanes on the signing commit**
- Who: orchestrator.
- Action: confirm every included profile's lane passed on the signing
  commit (1.2), not the release merge SHA (ruling A8; R-a; R8; 17:05
  ruling 2a; signing plan §5A.8 step 2).
- Check: one green receipt per profile.
- Stop: a failed lane drops that profile (R-a). Rebuild the candidate and
  redo 7.2-7.4.

**7.6 Recompute matches**
- Who: architect.
- Action: confirm step 1.3 matched on the signing commit for every
  included profile.
- Check: the candidate's fingerprints equal the 1.3 values.
- Stop: mismatch → no signing.

**7.7 Sign** [JOSH APPROVAL: one-way]
- Who: Josh.
- Action: sign seq 18 by the signing plan's procedure (§5A.8; R-f, signing
  plan §9D: only after the R-d recompute, every included lane passing on
  the signing commit, and the candidate check against R-b).
- Check: the signed journal shows seq 18 for 2026.11.0.
- Stop: any doubt → do not sign. FACT: there is no per-capability undo; a
  revoke withdraws every 2026.11.0 capability, and they cannot be re-added
  (ruling A8; recovery HAMCP-367).

---

## Phase 8: activation check (read-only)

**8.1 Health**
- Who: architect.
- Action: call `get_server_health`.
- Check: 2026.11.0 is admitted from signed seq 18 with 38 references (one
  fewer for each profile 7.5 dropped) and 0 mismatches.
- Stop: still seq 17 → stop and report. Never delete a cache or roll back the
  journal.

**8.2 Catalog**
- Who: architect.
- Action: call `tools/list`.
- Check: 114 tools, unchanged. No client refresh is needed (acceptance §6
  step 2).
- Stop: changed → stop and report.

---

## Phase 9: live acceptance, in order

- Order: reads, then configuration writes, then device configuration last
  (ruling B9).
- Full steps: acceptance §7.
- Every mutating step has its own approval from Josh.
- Clean up each step on 2.4.2-beta.8 as it ends (9.9). A full restore (RB4)
  also reverts Home Assistant; an Engineering-only restore (RB5) does not.

**9.1 Topology** (acceptance §7.1)
- Who: architect.
- Action: `get_thread_matter_topology()`, then again with
  `sections=["datasets"]`.
- Check: both sections are returned. No credentials, MAC, SSID or TLV
  appear. References change between calls.
- Stop: a secret appears → stop, do not share the output.

**9.2 Marketplace** (acceptance §7.2)
- Who: architect, Josh.
- Action: page through to the end.
- Check: the row total = `total_matches`, and `inventory_fingerprint` is
  the same on every page. Josh checks the total against the UI by eye.
- Stop: mismatch → stop.

**9.3 Security metadata** (acceptance §7.3)
- Who: architect, Josh.
- Action: `get_security_metadata()`.
- Check: users are pseudonymous and no secrets appear. Josh checks the
  counts against People.
- Stop: a secret appears → stop. Record PASS/FAIL only.

**9.4 Scene create and undo** [JOSH APPROVAL: each write] (acceptance §7.4)
- Who: architect, Josh.
- Action: plan, panel approval, apply, readback, then `rollback_change`,
  panel approval, apply.
- Check:
  - `succeeded_verified` both ways;
  - `created_identity` is `captured`;
  - the count returns to the pre-state.
- Stop:
  - Gate: the committed HAMCP-377 12c receipt (`12c-source.json`, sha256
    `74b26509179b4edfe412e017f90503ecde3fc1aba56d006b68a569a46258d474`),
    PASS. If it is missing or different on the merge commit → do not start
    (20:46 ruling 5).
  - Identity is not `captured` → Josh deletes the test scene in the UI.

**9.5 Helper create and delete** [JOSH APPROVAL: each write] (acceptance
§7.5)
- Who: architect, Josh.
- Action: create `input_boolean.hamcp_2_6_acceptance`. Wait at least 10 s,
  then `rollback_change`.
- Check: `succeeded_verified` both ways; the helper is gone from the list,
  states and registry.
- Stop: the 12c receipt is missing → do not start. The gate is kept
  (architect ruling D, relayed by Josh 2026-11-12). INFERENCE: 20:46 ruling
  5 names only the first scene-create write, but 12c also covers
  2.4.2-beta.7's handling of a 2.4.2-beta.8-written created-helper rollback
  plan. In 12c, case `helper_rollback_approved` was refused with
  `policy_snapshot_mismatch` and made no provider write. Its limits: source
  subject and a synthetic provider, not an image and not a house; only an
  approved helper rollback plan was run, not a pending one.

**9.6 Automation create rollback** [JOSH APPROVAL: each write] (D19;
acceptance §7.6)
- Who: architect, Josh.
- Action: create a throwaway automation with no trigger that can fire. Then
  `rollback_change`.
- Check: `succeeded_verified` both ways; the count returns to the
  pre-state.
- Stop: anything unexpected → stop.

**9.7 Target-kind refusals** [JOSH APPROVAL: each] (acceptance §5 step 3)
- Who: architect.
- Action: call `control_device_configuration` on a non-config switch, a
  diagnostic entity and a Tapo select.
- Check: each is refused for target kind (for example,
  `device_configuration_target_kind_not_admitted`), with no dispatch and
  states unchanged. `list_change_plans` shows no pending or approved plan
  (architect ruling E; see Phase 6).
- Stop: any dispatch, or a pending or approved plan left → stop. Do not run
  9.8.

**9.8 Eve plug device configuration** [JOSH APPROVAL: each step, in chat]
(acceptance §7.7)
- Who: Josh, architect.
- Gates:
  - the committed HAMCP-377 12b receipt (`12b-source.json`, sha256
    `fee3d5250fee91f0210d2ed2e778d132e83d2a468392d2768010ff9a0136fb46`),
    PASS;
  - the HAMCP-379 recovery containment, which ships in this release (#436;
    23:01 ruling 3).
- Action, on the master bedroom TV or kitchen Eve plug only:
  1. power-on behavior: `select_option`, then `undo`;
  2. child lock: on/off, then `undo`.

  Never use the beer fridge plug or the network cabinet plug. A blanket
  approval does not count (D21).
- Check:
  - each step is `succeeded_verified`, and each undo is
    `succeeded_verified` with `undone` true;
  - the main switch is unchanged;
  - at the end there is no hold and no non-terminal task.
- Stop:
  - `confirmation_required` → nothing was sent; tell Josh.
  - An uncertain result → poll `get_execution_task` at most every 10 s.
    Never resend.
- Not tested live: the number path (D20; see the release notes' Known
  limitations).

**9.9 Final readback**
- Who: architect.
- Action: run `list_change_plans`, `list_execution_tasks` and
  `get_server_health`.
- Check: no test objects are left, no pending plan, no non-terminal task, no
  hold.
- Stop: anything left → clean it up on 2.4.2-beta.8 with Josh's approval.

---

## Rollback decision points

Every rollback is **[JOSH APPROVAL]**. Recovery from a bad 2.4.2-beta.8 is
a restore from the full Home Assistant backup taken immediately before
deploy (2.6). In-place rollback to 2.4.2-beta.7 is no longer a supported
path (Josh's decision 3a). There are two restore paths:
- RB4, tier 4, primary: restore the full backup. It also reverts the Home
  Assistant objects created during acceptance. The full restore is
  untested.
- RB5, secondary: a partial restore of only the Engineering add-on, when
  Home Assistant changes made after deploy must be kept. Architect ruling B's
  orphan step applies only to this path.

The A2 result is recorded in 0.4: PASSED 2026-11-13 (Josh's decision 4).
A2 rehearsed only the RB5 kind of restore, at the same version
(2.4.2-beta.7 over 2.4.2-beta.7). Restoring a 2.4.2-beta.7 backup over an
installed 2.4.2-beta.8 and the full restore (RB4) remain untested.

The recovery choice here does not contradict
[`DOWNGRADE-VERSUS-BACKUP-RESTORE.md`](DOWNGRADE-VERSUS-BACKUP-RESTORE.md):
that document is a future decision that is not implemented (:3), and it
holds that a restore is not viable merely because an archive exists
(:43-44). This runbook makes no claim that either restore is proven; it
names the untested paths and reads back everything after a restore.

**Why in-place downgrade is unsupported.** These two 2.4.2-beta.7 findings
are the reasons. They are not a recovery procedure (Josh's decision 3a).
1. 2.4.2-beta.7 executes created-object rollback plans written by
   2.4.2-beta.8 as ordinary deletes. It runs automation and script rollback
   plans without the created-identity check and can approve pending ones
   (ruling 26; HAMCP-377 12c receipt).
2. An expired device-configuration lock left by 2.4.2-beta.8 faults
   governed execution on 2.4.2-beta.7. 2.4.2-beta.7 neither releases nor
   redispatches a lock it does not own (`f3/locks.py:329-342` at the tag:
   expiry alone never frees a key), and the shared `home_assistant:core`
   record blocks governed writes on other entities too (23:11 ruling A3;
   HAMCP-377 12b receipt).

FACT: `docs/BETA_DEPLOYMENT.md:392-393` says "Do not downgrade metadata in
place".

**RB-pre: before either restore**
1. Export the 2.4.2-beta.8 audit (`get_audit_log`) if 2.4.2-beta.8 answers
   reads. It is lost on restore (Josh's decision 3a). If it cannot answer,
   record that.
2. Repeat check A1 (0.5) immediately before the restore.
3. The loss of every Engineering record written since deploy is accepted:
   plans, receipts, audit and the trace-watch sidecar state.
4. Client refresh is planned for afterwards (110 tools).
5. The Idle check is not needed before a full restore (Josh's decision 3a).
   For RB5, see its Action step 2.

**RB1: after deploy, before signing**
- Who: Josh.
- Action: RB4. Use RB5 instead only when Home Assistant changes made after
  deploy must be kept.
- Check: `server_info` reports 2.4.2-beta.7; tools return to 110.
- Stop: the restore fails → stop, record it and tell Josh. A forward fix is
  the remaining route.

**RB2: after signing**
- Who: Josh.
- Action: as RB1. Seq 18 stays signed. It is outside the Home Assistant
  backup, so after either restore 2.4.2-beta.7 runs against seq 18 (Josh's
  decision 3b).
- Check: 2.4.2-beta.7 admits 33 of the 38 references (12a receipt).
- Stop: never revoke the entry to "undo" signing. Revocation and HAMCP-367
  stay the only undo for seq 18 (Josh's decision 3b).

**RB4: tier 4, full restore (primary)**
- Who: Josh. The architect makes the audit export in RB-pre.
- Method: restore, in the Home Assistant UI, the full backup from 2.6
  (Josh's decision 3a). Status: untested.
- Action:
  1. Do RB-pre.
  2. Restore the 2.6 full backup. Take the exact UI labels for a full
     restore from the Home Assistant UI at the time.
  3. Wait until Home Assistant and the Engineering add-on have started and
     the add-on log shows startup readiness complete.
- Check:
  - Engineering is back on 2.4.2-beta.7 with its pre-deploy data;
  - the acceptance objects (scenes, helpers, automations, device
    configuration) are back to their pre-deploy state;
  - seq 18 is still signed and 2.4.2-beta.7 admits 33 of the 38
    references, if seq 18 was signed.
  - INFERENCE: a full restore returns all of Home Assistant and every add-on
    in the backup to the time of 2.6, not only the acceptance objects. Any
    other change made in the house since then is lost. That consequence is
    for Josh to weigh.
- Stop:
  - Check A1 shows another digest → no restore. Report it.
  - The restore fails or Home Assistant does not start → stop and tell
    Josh. Read back everything after the restore.

**RB5: Engineering-only restore (secondary)**
- Who: Josh. The architect makes the list and the audit export.
- Method: a partial restore of only the Engineering add-on from the 2.6
  backup. Supervisor reinstalls the backed-up version and pulls its image by
  tag (architect ruling A). Status: rehearsed at the same version only (A2
  passed 2026-11-13, 0.4); a 2.4.2-beta.7 backup restored over an installed
  2.4.2-beta.8 is untested.
- Mandatory orphan step (architect ruling B, relayed by Josh 2026-11-12;
  applies only to this path, Josh's decision 3a). An add-on restore does
  not revert Home Assistant.
  1. List every object created or changed during acceptance (scene, helper,
     automation, device-configuration entity), with id and pre-state.
  2. Clean them up through 2.4.2-beta.8 if it is functional. Otherwise
     record them for manual cleanup.
  3. The audit export is RB-pre step 1.
- Action:
  1. Do RB-pre and the orphan step. Each cleanup write keeps its own
     approval, as in Phase 9.
  2. Run the Idle check, if 2.4.2-beta.8 answers reads. INFERENCE, not
     ruled (see the Idle check): a partial restore replaces Engineering's
     records, so a task in flight is lost with them while its effect on the
     house stays. If 2.4.2-beta.8 cannot answer, record that the check could
     not be run; whether to restore without it is Josh's decision.
  3. Restore only the Engineering add-on from the 2.6 backup. Take the exact
     UI labels from the Home Assistant UI at the time.
- Check: Engineering is back on 2.4.2-beta.7 with its pre-deploy data. Every
  Engineering record since deploy is lost. Home Assistant is not reverted:
  anything the orphan step did not clean up is still there.
- Stop:
  - The orphan step is not done → no restore.
  - Check A1 shows another digest → no restore. Report it.
  - FACT: the downgrade-versus-restore decision is not implemented
    (`docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md:3`), and an archive
    alone does not make a restore viable (`:43-44`). Read back everything
    after the restore.

**RB-never**
- Never remove lock records by any method; it prevents settlement (22:23
  ruling 1).
- Never roll back the journal or delete a cache.

**RB-after**
- Who: Josh, architect.
- Action: refresh every client.
- Check: `server_info` reports 2.4.2-beta.7 and the catalog is 110.
  Plan and task totals may be lower by the terminal records older than 90
  days (see "Expected count drop after a restart"); that is not a fault.
- Stop: mismatch → report.
