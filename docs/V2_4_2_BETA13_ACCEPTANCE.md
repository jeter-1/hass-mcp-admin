# Engineering 2.4.2-beta.13 acceptance

**Status: in progress.** This contract covers the 2.9.1 build, released as
2.4.2-beta.13. It follows 2.4.2-beta.12 (the 2.9.0.1 hotfix, HAMCP-470,
#488). Its content lands through item pull requests into the `release/2.9.1`
integration branch. The final release pull request completes every section
marked **pending** before it is offered for review.

See also the release notes:
[`V2_4_2_BETA13_RELEASE_NOTES.md`](V2_4_2_BETA13_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.13`, following 2.4.2-beta.12. The
architect's block of 2026-11-23 names this version for the 2.9.1 release
path, and the value is authorized on that path only. "2.9.1" names the build,
not the version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.12 (#488),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.13 is materialized once on `release/2.9.1`, at its catch-up with
`main` (b4dd3da3), and item pull requests carry no version change of their
own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.9.1`. An item
or part that has not landed is not part of this release, and this contract
makes no claim about its behavior. The pull request, merge commit and state
of each row were read from GitHub on 2026-11-24; the final release pull
request binds the evidence of each landed item. The release notes describe
each row in more detail.

| Item | Pull request | Merge | State | What landed | What did not |
| --- | --- | --- | --- | --- | --- |
| HAMCP-456 | #487 | 584b31da | merged | Version-gate lint | No runtime change (tests only) |
| HAMCP-458 | #486 | c5f160c0 | merged | Release tooling: refuse a guessed base, check the test venv, readiness sweep | No runtime change (tooling only) |
| HAMCP-472 | none | none | moved to native search | Diagnosis: not reproduced (the HA Admin replay's domain-only and query-plus-domain calls returned entities); source cause found (a call that passes `search_types` still skips entities on ha-mcp 9.2.0) | No 2.9.1 code; that path stays until the native provider ships |
| HAMCP-481 | #491 | e51968a2 | merged, design only | [`HAMCP_481_NATIVE_SEARCH_DESIGN.md`](HAMCP_481_NATIVE_SEARCH_DESIGN.md), revision 4 (draft for review, not approved) | The build, planned for the next release |
| HAMCP-473 | #492 | 8ed1b99b | merged | `configuration_integrity_analysis` compact detail level and severity filter, bound to the cursor | The audit record does not record `severities` (disclosed) |
| HAMCP-474 D5, D6 | #497 | 0f52b0e0 | merged | `automation_reliability_batch_analysis` names limited sources (`source_limitations`) and carries root-cause detail | Near the bound, one automation fewer per response (disclosed); D2-D4 split to HAMCP-482, HAMCP-483, HAMCP-484 |
| HAMCP-474 D1 | #496 | none | deferred to HAMCP-487; #496 left open as a draft | Nothing | Exact coverage for deep automations with a long configuration path (HAMCP-487) |
| HAMCP-475 | #493 | 6adccba0 | merged | `get_audit_log` pages that fit the response limit, a `_page` trailer and a signed `cursor` | The rotated `audit.jsonl.1` is not read; cursors do not survive a restart |
| HAMCP-476 | #498 | c6d6b538 | merged | `get_automation_run_counts`, one read-only native tool | Failed-condition and mode-dropped runs are not counted; no paging |
| HAMCP-293 | #494 | 99b41cc5 | merged, middle path | Exact automation update reasons name what changed; the legacy level is kept | No level is lowered (lowering rule: HAMCP-486) |
| HAMCP-292 | #495 | 010de1ff | merged | Dashboard update review projection keeps strings whole up to 2,000 characters | Secret detection unchanged; split-field secrets still not detected (pre-existing) |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.13` materialized, with exact document resolution;
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
release. Josh approves each step, and the final release pull request states
which steps are reads.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.13`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** Reads in one sitting, one step per landed runtime item.
   Two steps create a plan only. No step approves or applies a plan.
   1. **Integrity paging** (HAMCP-473). `configuration_integrity_analysis`
      with `detail_level` `compact`, walked by cursor to its last page, then
      one call with `severities` naming a single severity. Expected: compact
      pages of up to 100 findings carrying only the allowlisted keys; the
      narrowed call carries `severity_filter` with `excluded_finding_count`
      on every page, and its `final_assessment` still counts the hidden
      findings. Offline: `tests/test_hamcp473_integrity_paging.py`.
   2. **Audit-log paging** (HAMCP-475). `get_audit_log` with no cursor, then
      with the returned `next_cursor` for at least one more page. Expected:
      each response fits the response limit and ends with exactly one
      `_page` line (`returned`, `next_cursor`, `complete`); the next page
      holds older records, none repeated or cut. Offline:
      `tests/test_hamcp475_audit_log_paging.py`.
   3. **Run counts** (HAMCP-476). `get_automation_run_counts` with
      `automation_ids` naming up to 10 automations, then with
      `all_automations`, both over the default 24-hour window. Expected: a
      count for every automation, with first and last run times where it
      ran; all mode ranked by runs descending, ties by ID; `partial` only
      with a non-empty `partial_reasons`. A refusal
      (`automation_count_exceeds_bound` or `response_size_exceeds_bound`)
      does not pass this step; record its reason and counts. Offline:
      `tests/test_hamcp476_automation_run_counts.py`.
   4. **Change-scoped risk reasons** (HAMCP-293), plan only.
      `create_configuration_plan` for an update to one existing automation
      that already contains a high-risk call, changing a different step.
      Expected: the legacy level and reasons first, unchanged; then a
      `Changed:` line, a `Changed action steps:` line, and a
      `High-risk call present, unchanged` line naming the call by service and
      path. Do not approve or apply the plan. Offline:
      `tests/test_hamcp293_change_scoped_risk.py`.
   5. **Long dashboard description** (HAMCP-292), plan only.
      `create_dashboard_update_plan` with a description of 513 to 2,000
      characters that contains no secret. Expected: the plan is created, not
      refused with `plan_contains_prohibited_sensitive_data`, and its review
      shows the description whole. Do not approve or apply the plan.
      Offline: `tests/test_hamcp292_dashboard_review_projection_bound.py`.
   6. **Reliability batch detail** (HAMCP-474 D5, D6).
      `automation_reliability_batch_analysis` over a few automations.
      Expected: `source_limitations` names each limited source with
      `source_type`, `reason`, `affects_result_status` and `item_count` (a
      limited system log reads `retention_unknown`, with
      `affects_result_status` false); each root-cause summary carries
      `confidence`, `current_state`, `representative_configuration_paths`,
      `recommended_investigation` and `governed_change_required`; and
      `root_cause_detail` is `full` or `reduced`. Offline:
      `tests/test_hamcp229_reliability_batch.py`.

   There are no lifecycle steps. The two plan-only steps add a plan record
   in Engineering and change nothing in Home Assistant; neither plan is
   approved or applied.

   Two checks planned in the 2.9.1 re-scope are not in this list: search
   with a domain filter (HAMCP-472 moved to native search, HAMCP-481) and
   coverage of the three large automations (HAMCP-474 D1, deferred to
   HAMCP-487).
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
