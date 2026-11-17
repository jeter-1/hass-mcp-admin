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
5. installed governed-change acceptance (E043).

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
2. **Item checks.** **Pending.** One read-only check per landed item.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## 5. Installed governed-change acceptance: E043 (separately authorized)

This stage writes. It runs only after section 4 passes and only when Josh
separately authorizes this governed change and the Core restart. Use a test
object that Josh names, such as the description of a dedicated test
automation. Add the object to the Deploy step 0 list.

The restart must leave Core authority identical. Do not combine it with a
Core, HACS component or ha-mcp app update, or with an integration change.
Any of those changes the authority, and the plan is then correctly voided.

1. **Before the restart.** Create a governed plan on the test object (for
   example `create_configuration_plan` updating its description). Josh
   approves it externally at its exact hash. Do not apply it.
2. **Restart Home Assistant Core.** Wait until `get_server_health` shows the
   Core readmission `recovery_state` as `restored`, with `last_restored_at`
   later than the restart.
3. **After the restart.** Apply the approved plan with `apply_change_plan`.
4. **Expected result:**
   - `get_execution_task` shows the task `succeeded_verified`, with exactly
     one dispatch and an exact read-back;
   - the Engineering add-on log has one `core_authority_restored` record for
     this restart, with `reason: core_authority_identical` and
     `plans_preserved: true`.

PASS needs every expected value. On any other outcome, stop and record the
plan, the task and the log record in the release task. Do not retry the step
on another object. Examples:
- the plan is refused as stale or voided;
- the record shows `core_authority_changed` (with `changed_components`) or
  `core_plan_window_capacity`;
- a dispatch count other than one.

Whether to keep the test change or revert it is Josh's decision. A revert is
its own governed plan.

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
