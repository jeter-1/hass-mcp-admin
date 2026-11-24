# Engineering 2.4.2-beta.13 acceptance

**Status: released.** This contract covers the 2.9.1 build, released as
2.4.2-beta.13. It follows 2.4.2-beta.12 (the 2.9.0.1 hotfix, HAMCP-470,
#488). Its content landed through item pull requests into the `release/2.9.1`
integration branch, and release pull request #500 carried it to `main`.
Sections 1 to 3 are recorded below. Section 4, the installed read-only
acceptance, passed on 2026-11-24; its result is recorded at the end of
section 4.

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
declaration left. It was published on 2026-11-24 (section 3), and Josh
deployed it the same day (as relayed by the architect).

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
| HAMCP-473 | #492 | 8ed1b99b | merged | `configuration_integrity_analysis` compact detail level and severity filter, bound to the cursor | The audit record does not record `severities`, and the health counters add a filtered query's filtered counts, not the classified totals (both disclosed) |
| HAMCP-474 D5, D6 | #497 | 0f52b0e0 | merged | `automation_reliability_batch_analysis` names limited sources (`source_limitations`) and carries root-cause detail | Near the bound, one automation fewer per response (disclosed); D2-D4 split to HAMCP-482, HAMCP-483, HAMCP-484 |
| HAMCP-474 D1 | #496 | none | deferred to HAMCP-487; #496 left open as a draft | Nothing | Exact coverage for deep automations with a long configuration path (HAMCP-487) |
| HAMCP-475 | #493 | 6adccba0 | merged | `get_audit_log` pages that fit the response limit, a `_page` trailer and a signed `cursor` | The rotated `audit.jsonl.1` is not read; cursors do not survive a restart |
| HAMCP-476 | #498 | c6d6b538 | merged | `get_automation_run_counts`, one read-only native tool | Failed-condition and mode-dropped runs are not counted; no paging |
| HAMCP-293 | #494 | 99b41cc5 | merged, middle path | Exact automation update reasons name what changed; the legacy level is kept | No level is lowered (lowering rule: HAMCP-486) |
| HAMCP-292 | #495 | 010de1ff | merged | Dashboard update review projection keeps strings whole up to 2,000 characters | Secret detection unchanged; split-field secrets still not detected (pre-existing) |

## 1. Source and offline acceptance

**Recorded.** Scope and authority come from the architect's 2.9.1 blocks
(HAMCP-455), as the release notes describe. Each item pull request's body
carries its owner decision and scope, its validation tier, its tests and its
review record.

Release pull request #500 binds each item's merged head, merge commit and
head CI run (table below). The review rounds are summarized from #500, which
took them from each item pull request and the review copies under
`~/handoffs/architect/reviews/`. The Cursor rounds ran on the hardened review
unit with `grok-4.7-xhigh-fast` where the pull request records the model.
Cursor and Codex are non-Claude reviewers. The architect's checks are Claude
self-family checks, not independent review.

| PR | Scope | Merged head, merge | Reviewer and final round | CI on the merged head |
| --- | --- | --- | --- | --- |
| #486 | HAMCP-458, release tooling (tooling only) | 7a689137, c5f160c0 | Cursor r1 CHANGES_REQUIRED; one batch; r2 READY | 45307064839 success |
| #487 | HAMCP-456, version-gate lint (tests only) | 5f70c96d, 584b31da | Cursor r1 READY_WITH_FOLLOW_UP at 8c9c0c56; no delta round, as the architect directed | 45311882791 success |
| #490 | HAMCP-455, catch-up with `main` and the beta.13 declaration | 6f53ca9c, 774328e0 | no independent review; the architect's check | 45330504046 success |
| #492 | HAMCP-473, integrity compact paging and severity filter | d1f3757a, 8ed1b99b | Cursor r1 CHANGES_REQUIRED; one batch; r2 READY | 45374895693 success |
| #493 | HAMCP-475, audit-log paging | c7e462d7, 6adccba0 | Cursor r2 READY; Codex r2 one Medium, with one extra batch approved by Josh (relayed); Codex r3 one new Medium, closed by the test-only follow-up c7e462d7, which the architect verified | 45405645749 success |
| #491 | HAMCP-481, native search design (design only) | 70a570b3, e51968a2 | Cursor r2 CHANGES_REQUIRED on 3ace9227; docs-only revisions 3 and 4, delta-checked by the architect | 45424200589 success |
| #495 | HAMCP-292, dashboard review projection | 30e53bfe, 010de1ff | Cursor r1 approve (two Lows, fixed); Codex r1 READY_WITH_FOLLOW_UP (fixed); the architect verified the batch | 45431887769 success |
| #497 | HAMCP-474 D5 and D6, reliability batch detail | ad6a4842, 0f52b0e0 | Cursor r1 READY, no findings | 45428679691 success |
| #498 | HAMCP-476, `get_automation_run_counts` | c230c77d, c6d6b538 | Cursor r1 CHANGES_REQUIRED (three Highs); delta r2 READY | 45449607368 success |
| #494 | HAMCP-293, exact-update risk reasons (middle path) | 58dba5a6, 99b41cc5 | Cursor and Codex r1; re-scoped by Josh (relayed); Cursor r2 CHANGES_REQUIRED (two Mediums, reason text), corrected in 58dba5a6 and verified by the architect | 45451964420 success |
| #499 | HAMCP-488, beta.13 docs rows (docs only) | 2f608456, 34bab9ad | no independent review; the architect's check | 45465850805 success |

#499's merge commit 34bab9ad is the final release head.

**Runtime changes.** Between `main` at 3d7c1c67 and the release head
34bab9ad, 20 files under `hass_mcp_engineering_beta/` changed (+1,592, −50):
the CHANGELOG and 19 protected files. Each protected file came in through
the item pull request #500 names for it, under that pull request's named
protected authorization, and was declared to the Evidence run with
`-AuthorizedProtectedPath`:
- `config.yaml` and `version.py`: the version only (#490);
- the new `automation_runs/` package and `tools/automation_runs.py`, with
  `capabilities.py`, `clients/logbook.py`, `ha_core_readmission/routes.py`,
  `providers/models.py`, `providers/routing.py` and `tools/registry.py`
  (#498);
- `routing.py` (#493, #498) and `tools/compatibility.py` (#493);
- `integrity/service.py` and `tools/analysis.py` (#492);
- `reliability/batch.py` (#497);
- `governance/risk.py` (#494) and `governance/service.py` (#494, #495).

There is no change to `.github/workflows/`, `.github/codex/`, `.release/`,
`hass_mcp_admin/`, `repository.yaml`, `.coderabbit.yaml` or the Dockerfile.

**Release head.**
- `2.4.2-beta.13` is materialized on `release/2.9.1` (#490). The release
  transition checks passed on 34bab9ad, and document resolution is `exact`
  for this document and the release notes.
- **Evidence tier: passed** on the final release head 34bab9ad (base `main`
  3d7c1c67), 2026-11-24 14:24-15:22 CST. It ran
  `check.ps1 -Tier Evidence -Parallel 4 -BaseRef origin/main` with
  `-AuthorizedProtectedPath` for the protected files in the diff: 18 of 18
  steps passed, 14,100 tests ran and 19 were skipped, in 11 shards.
  `validation.json` sha256
  `2be12ac51c82b7fcfcd7d6e533211ffa5c8ececc009be0e2fa0144e2da8bbc76`,
  `plan_sha256`
  `a998fcc5c24ce9a6d598a9ecff1922c0a7fcd14c16e62d0b6389fdf85197fa40`. Peak
  memory was 1.9 GB, under the 3 GiB cap. An earlier launch stopped in
  preflight before any test ran, because no `-BaseRef` was passed in a
  detached checkout; the run above is the one Evidence run.
- **Readiness sweep: not ready on one part.**
  `scripts/release_readiness.py` returned rc=1: the support window and the
  contract matrix were ready, but Core evidence had no run of this head.
  `core-release-evidence.yml` runs on pull requests only for its path
  filter, and this release diff matches none of it. The newest run was
  `main`'s scheduled run 45479533262 (success) on 3d7c1c67. 2.9.1 was
  accepted without a head run (as relayed by the architect). The follow-up
  is HAMCP-496.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Passed.** Release pull request #500 (`release/2.9.1`
at 34bab9ad into `main` at 3d7c1c67). The runs were CI 45490799988
(`validate`, the 11 unit shards, Real HA `ha-2026-11-0`, both exact-image
9.2.0 lanes and the manifest gate) and Policy locks 45490799860, both
`success` on 34bab9ad. GitHub's check rollup for #500 at merge showed 26
successful and 4 skipped, none failed. No Core evidence run exists for this
head (section 1).

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Done.**
- #500 was merged by the head-bound Ready workflow on 2026-11-24 at 16:09
  CST, as `main` f7e03478c886121e9362c34db52b08e621375b5d. Its tree differs
  from 34bab9ad only by #489's beta.12 post-release docs
  (`docs/V2_4_2_BETA12_ACCEPTANCE.md` and
  `docs/V2_4_2_BETA12_RELEASE_NOTES.md`), which `main` already carried.
- Workflow run 45498225361 ("Publish reviewed Engineering release",
  `success`, 16:09-16:37 CST) published that commit:
  - version image `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.13`;
  - commit image
    `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-f7e03478c886121e9362c34db52b08e621375b5d`;
  - OCI manifest digest
    `sha256:bf7f5c1d7ecb71c4824447f38e77c200cb0160e020a07400ea3ed35b262d5af7`;
  - annotated tag `v2.4.2-beta.13` at f7e03478;
  - a GitHub pre-release, published at 16:37 CST.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. Every step is a read, and Josh approves each step.

1. **Identity.** `server_info` and `get_server_health` report:
   - `2.4.2-beta.13`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** Reads only, in one sitting. No step creates, approves or
   applies a plan.
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
   4. **Reliability batch detail** (HAMCP-474 D5, D6).
      `automation_reliability_batch_analysis` over a few automations.
      Expected: `source_limitations` names each limited source with
      `source_type`, `reason`, `affects_result_status` and `item_count` (a
      limited system log reads `retention_unknown`, with
      `affects_result_status` false); each root-cause summary carries
      `confidence`, `current_state`, `representative_configuration_paths`,
      `recommended_investigation` and `governed_change_required`; and
      `root_cause_detail` is `full` or `reduced`. Offline:
      `tests/test_hamcp229_reliability_batch.py`.

   There are no lifecycle steps. HAMCP-293 and HAMCP-292 are not checked
   live: both act only when a plan is created, and creating a plan is not a
   read. Their offline evidence is `tests/test_hamcp293_change_scoped_risk.py`
   (#494) and `tests/test_hamcp292_dashboard_review_projection_bound.py`
   (#495).

   Two checks planned in the 2.9.1 re-scope are not in this list: search
   with a domain filter (HAMCP-472 moved to native search, HAMCP-481) and
   coverage of the three large automations (HAMCP-474 D1, deferred to
   HAMCP-487).
3. **Tool catalog.** A fresh `tools/list` lists 87 Engineering tools,
   including `get_automation_run_counts`, with none removed, and the same
   delegated tools as beta.12 listed before the deploy (see the release
   notes, "Tool catalog").

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

### Result: passed (2026-11-24)

HA Admin ran this stage on 2026-11-24, 16:52-16:58 CST, after Josh deployed
the release: reads only, on Home Assistant Core 2026.11.0 with ha-mcp 9.2.0.
Every step passed. The values below are HA Admin's report as relayed by the
architect, who holds the full report; the author of this record did not
observe them. HA Admin numbered the steps 1 to 6; the numbers in brackets are
this section's.

1. **Identity** [1]: passed through `server_info`. `get_server_health`
   reports no build SHA or `dirty` flag, so step 1's identity was confirmed
   on `server_info` alone (follow-up HAMCP-497).
2. **Integrity paging** [2.1, HAMCP-473]: the compact walk returned 14 pages
   and 1,310 findings; page 3 was clamped to 97 findings.
3. **Audit-log paging** [2.2, HAMCP-475]: two pages; page 2 held the older
   records, with none repeated.
4. **Run counts** [2.3, HAMCP-476]: `automation_ids` mode, 10 automations,
   complete, with 3 GET requests; `all_automations` mode, 124 automations,
   complete, with 15 GET requests and one states request.
5. **Reliability batch detail** [2.4, HAMCP-474 D5, D6]:
   `source_limitations` named `system_log` with `retention_unknown`, and
   `root_cause_detail` was `full`.
6. **Tool catalog** [3]: 115 tools, 87 Engineering and 28 delegated, with
   the delegated catalog fingerprint unchanged.

HAMCP-293 and HAMCP-292 are confirmed offline only, by design: they act only
when a plan is created, and creating a plan is not a read. Both rows stay
open until a real plan is created on 2.4.2-beta.13.

The acceptance observations are filed as Backlog rows for next-release
triage; none of them failed a step:
- HAMCP-490: compact integrity rows repeat 35 `finding_id` values, all
  `unresolved_dynamic_reference` (1,088 unique of 1,197 rows over 12 pages,
  as reported).
- HAMCP-491: under `severities`, `unresolved_dynamic_reference_count` is not
  filtered (295 with `finding_count` 0).
- HAMCP-492: compact integrity pages are about 60-63 KB, and clients write
  them to disk.
- HAMCP-493: in the batch, every root-cause summary had `current_state` null
  and `representative_configuration_paths` empty (6 of 6).
- HAMCP-494: two batch root-cause groups with the same title cannot be told
  apart.
- HAMCP-495: batch items were partial through
  `lifecycle.coverage_complete=false` with no stated reason.
- HAMCP-496: the readiness sweep refuses a release head whose diff matches
  none of `core-release-evidence.yml`'s path filters (section 1).
- HAMCP-497: `get_server_health` lacks the build SHA and `dirty` flag (step
  1).

The audit record still does not record `severities` (already disclosed for
HAMCP-473; no new row).

## Rollback

There is no in-place rollback. Recovery is restoring the pre-deploy full Home
Assistant backup, or the VM 400 snapshot, both taken before the deploy. That
returns Engineering 2.4.2-beta.12 with ha-mcp 9.2.0.
- Target: tag `v2.4.2-beta.12`, commit
  b4dd3da332b381d81df9ec1fd6f72e2be06a527a, image
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.4.2-beta.12`, OCI digest
  `sha256:916f941c052cdbe9516cce8f22ff68e7682754dcccfb3518960d245983e4d634`
  (2.4.2-beta.12 acceptance, section 3, on `main`).
- Restoring removes `get_automation_run_counts`, the integrity and audit-log
  paging, the batch detail and the new risk reasons.
- Export `get_audit_log` before any restore.
- In-place downgrade from beta.13 to beta.12 is not supported and is
  unproven.
- CI runs no in-place rollback lane: #461 retired it (HAMCP-405) and allows
  an empty rollback slot (HAMCP-406, restore-first recovery).
- No landed item states a persisted-format change. #492, #493 and #498 state
  none. #494 adds risk reasons to new plans only, and a plan stored before it
  keeps its hash. #495 leaves the stored plan record and its scan unchanged.
  That is not a downgrade proof.
- **Safety.** Restoring the backup or snapshot returns Engineering, its
  storage and Home Assistant together to the backed-up state. It also
  reverts every Home Assistant change made since it was taken. Whether to
  restore, and which, is Josh's decision.
