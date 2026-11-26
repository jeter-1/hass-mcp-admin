# Engineering 2.4.2-beta.14 acceptance

**Status: in progress.** This contract covers the 2.10.0 build, released as
2.4.2-beta.14. It follows 2.4.2-beta.13 (the 2.9.1 build, HAMCP-455, #500).
Its content lands through item pull requests into the `release/2.10.0`
integration branch. The final release pull request completes every section
marked **pending** before it is offered for review.

See also the release notes:
[`V2_4_2_BETA14_RELEASE_NOTES.md`](V2_4_2_BETA14_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.14`, following 2.4.2-beta.13. Josh approved
the 2.10.0 plan on 2026-11-25, including this version, and the value is
authorized on the 2.10.0 release path only. "2.10.0" names the build, not the
version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.13 (#500),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.14 is materialized once on `release/2.10.0`, created from `main`
at f48c6fb9, and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.10.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-500 | The 2.4.2-beta.14 declaration, materialized in all three version authorities | landed (#504) |
| HAMCP-502 | `get_history` and incident activity reads bounded with an end time, so windows over 24 hours are complete; `minimal=False` history returns attribute-only changes; the incident reliability row follows a partial bundle; `get_error_log` flags a full system log ring | landed (#505); `tests/test_hamcp502_history_end_time.py` |
| HAMCP-462 | Core mirror pins refreshed to 2026.11.3, the trust-root seq 19 copy and the support-window rotation | landed (#508) |
| HAMCP-490, HAMCP-491 | Configuration integrity: a unique `finding_id` for each dynamic finding, and the unfiltered dynamic reference count labeled in `counter_semantics` | landed (#510); `tests/test_hamcp490_integrity_dynamic_ids.py` |
| HAMCP-493 | Reliability analysis summary root causes: `affected_step` in the single tool and the batch, and `current_state` `disabled` for a disabled automation | landed (#511); `tests/test_hamcp493_reliability_summary_location.py` |
| HAMCP-503 | Reliability trace retention reported as partial, and the trace watch retention-gap counter | landed (#509); `tests/test_hamcp503_trace_retention.py`, `tests/test_hamcp503_trace_watch_retention_gap.py` |
| HAMCP-497, HAMCP-498 | `get_server_health` `server.build_sha` and `build_dirty`; `release_registry` `last_refresh_at` and `refresh_interval_seconds`; a Core-bound refusal in an open Core restart window is retryable and names the window | landed (#512); `tests/test_hamcp497_health_build_identity.py`, `tests/test_hamcp498_core_restart_retryable.py` |
| HAMCP-508 | Bounded reads disclosed: list tools `total` and `truncated`; incident row bounds partial with `items_withheld` and `items_malformed`, and omitted-count keys; dependency indirect-walk gap and full dynamic count; `list_automation_traces` object reply (breaking: `traces`, `returned`, `source_truncated`, `retention`), with a full default ring at six runs and the oldest run absent | landed (#515); `tests/test_hamcp508_incomplete_results.py` |
| HAMCP-505 | The Core-evidence pull request acceptance target taken from the support window's current Core (2026.11.3). CI scripts and tests only | landed (#514) |
| HAMCP-481 W1 (built as HAMCP-501) | Native search scoring port, inert: no tool imports it | landed (#506) |
| HAMCP-481 W2 | Native search fixed Core reads, inert | landed (#507) |
| HAMCP-481 W3 | Native search engine, inert | landed (#513) |
| HAMCP-481 W4 | Native search sub-call page readers, inert | landed (#516) |
| HAMCP-481 W6 | The `ha_search` cutover to native search: the delegated `ha_search` internal-only, sub-call body matching (S1), the numeric and scrub withholding, and the page-size ladder | landed (#517); `tests/test_hamcp501_native_search_cutover.py`, `tests/test_hamcp481_native_search_engine.py`, `tests/test_hamcp501_w6_f1_body_matching.py` |
| HAMCP-481 W6b | Native search component-route parity | [fill: #519 state] |
| HAMCP-481 W7 | Native search: the design doc update | [fill: PR and state] |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.14` materialized, with exact document resolution;
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
   - `2.4.2-beta.14`;
   - the merged release SHA as build SHA;
   - `dirty: false`;
   - `upstream_version` `9.2.0` and `core_version` `2026.11.3`, the
     support window's current pair (HAMCP-462).
2. **Item checks.** Reads only, in one Engineering process, since signed
   cursors and the trace-watch counter are per process:
   - **HAMCP-497.** `get_server_health` reports `server.build_sha` equal to
     the merged release SHA and `server.build_dirty` `false`;
     `release_registry` reports `last_refresh_at` (a UTC time after the
     deploy) and `refresh_interval_seconds`.
   - **HAMCP-502.** `get_history` for one busy entity over about 30 hours
     ending now returns the full span, with its first change near the window
     start rather than cut at 24 hours; `get_history_slices` over the same
     entity and window gives the same change count and the same first and
     last change times.
   - **HAMCP-490, HAMCP-491.** `configuration_integrity_analysis` with
     `detail_level` `compact`, followed by `next_cursor` to the end: every
     `finding_id` is unique across the pages, and `counter_semantics` labels
     `unresolved_dynamic_reference_count` as unfiltered.
   - **HAMCP-503 TB-1, HAMCP-493.** `automation_reliability_analysis` on an
     automation that ran more than 5 times in the lookback (chosen first with
     `get_automation_run_counts`, also a read) reports `collection_state`
     `retention_limited`, a partial source, `retention_coverage` `partial`
     and a warning naming `trace.stored_traces`. Each summary root cause, if
     any, carries `affected_step`, and a disabled automation's group, if the
     scope includes one, reports `current_state` `disabled`, not `null`.
   - **HAMCP-503 TB-3.** `set_automation_trace_watch` with `action` `list`
     only: each watch row carries `retention_gap_polls` and
     `last_retention_gap_at` (`null` is valid). With no watch, record that and
     add none, since adding a watch is not a read.
   - **HAMCP-508.** `list_automations` reports `returned`, `total` and
     `truncated`, and `list_automation_traces` on the automation above returns
     `traces`, `returned`, `source_truncated` and `retention`, with
     `ring_full` `true`.
   - **HAMCP-481.** Each native search read repeats a read of the HA Admin
     baseline captured on beta.13 (2026-11-25 22:15 CST, sha256 `fe9e5a7e`)
     with the same arguments. A read passes when it matches the baseline or
     differs only as the native search parity list in the release notes
     says; any other difference is a failure, not a pass with a note. The
     order of equal scores may differ only where that list says so.
     - Entities: the domain, query, partial-id, area and state reads return
       the baseline's totals and first rows, with `score` and `match_type`
       where the baseline had them. A complete read reports
       `metadata.completeness` `complete`, and the visibility caveat is
       replaced by the disclosed no-filter note (HAMCP-499).
     - Configuration: a query for an entity used by automations returns the
       entity and the same automations, with `match_in_config` true and
       `match_in_references` true for those that use it. A paged query
       (`limit` 5, then `offset` 5) returns the same page rows with no
       overlap, and `offset` and `limit` are present. With `include_config`
       true no body is returned and the withheld count is present. A domain
       filter with a query reports configuration skipped (`config_skipped`).
     - Sub-call buckets: the dashboard and helper reads return the
       baseline's rows, the flow helper included; a scene word from the house
       finds its scene; and a query matching nothing gives every bucket 0 and
       `partial` false.
     - The audit shows native attribution, and sub-call page events only for
       the buckets searched.
     - [fill: W6b's parity reads (the composed `name` and the tier scorer).]

   Not checked live:
   - HAMCP-498 needs an open Core restart window, and causing one is not a
     read; its evidence is `tests/test_hamcp498_core_restart_retryable.py`.
   - HAMCP-462 acts in CI and admission; the pair in step 1 is its only
     live trace.
   - HAMCP-505 changes CI only.
   - HAMCP-481 S1, the configuration-body oracle, is never probed live,
     since probing it would be the attack. Its evidence is
     `test_no_configuration_body_read_is_reachable` in
     `tests/test_hamcp481_native_search_engine.py` and the body-match tests
     in `tests/test_hamcp501_w6_f1_body_matching.py`.
   - HAMCP-481 floors and area aliases, where the house has none: fixtures
     only.
   - HAMCP-481 page-size step-down (150, 30, 6, 1): `LadderTests` only.

   There are no lifecycle steps.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares, with exactly one `ha_search` and no
   `ha_mcp__ha_search`. At #517 the catalog is 114 tools: 88 Engineering
   and 26 delegated (beta.13 as observed live: 115, 87 and 28). [fill:
   confirm after W6b and W7]

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
