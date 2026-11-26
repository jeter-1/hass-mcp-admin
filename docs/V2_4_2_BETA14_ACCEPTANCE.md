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
| HAMCP-502 | `get_history` and incident activity reads bounded with an end time, so windows over 24 hours are complete; `minimal=False` history returns attribute-only changes; the incident reliability row follows a partial bundle; `get_error_log` flags a full system log ring | landed (#505); `tests/test_hamcp502_history_end_time.py` |
| HAMCP-462 | Core mirror pins refreshed to 2026.11.3, the trust-root seq 19 copy and the support-window rotation | landed (#508) |
| HAMCP-490, HAMCP-491 | Configuration integrity: a unique `finding_id` for each dynamic finding, and the unfiltered dynamic reference count labeled in `counter_semantics` | landed (#510); `tests/test_hamcp490_integrity_dynamic_ids.py` |
| HAMCP-493 | Reliability analysis summary root causes: `affected_step` in the single tool and the batch, and `current_state` `disabled` for a disabled automation | landed (#511); `tests/test_hamcp493_reliability_summary_location.py` |
| HAMCP-503 | Reliability trace retention reported as partial, and the trace watch retention-gap counter | landed (#509); `tests/test_hamcp503_trace_retention.py`, `tests/test_hamcp503_trace_watch_retention_gap.py` |
| HAMCP-497, HAMCP-498 | `get_server_health` `server.build_sha` and `build_dirty`; `release_registry` `last_refresh_at` and `refresh_interval_seconds`; a Core-bound refusal in an open Core restart window is retryable and names the window | landed (#512); `tests/test_hamcp497_health_build_identity.py`, `tests/test_hamcp498_core_restart_retryable.py` |
| HAMCP-508 | Bounded reads disclosed: list tools `total` and `truncated`; incident row bounds partial with `items_withheld` and `items_malformed`, and omitted-count keys; dependency indirect-walk gap and full dynamic count; `list_automation_traces` object reply (breaking: `traces`, `returned`, `source_truncated`, `retention`), with a full default ring at six runs and the oldest run absent | landed (#515); `tests/test_hamcp508_incomplete_results.py` |
| HAMCP-481 | Native search (entities, automations and scripts) | not landed |

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
   - `dirty: false`.
2. **Item checks.** **Pending.** One read per landed item. The planned checks
   are:
   - a `get_history` read over more than 24 hours returns the full span;
   - integrity analysis compact findings have distinct identities and
     filtered counts;
   - reliability analysis reports current state and paths;
   - health reports the build identity and registry refresh time;
   - native search returns entities, automations and scripts.

   There are no lifecycle steps.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
