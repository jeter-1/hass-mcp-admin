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
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-456 | Version-gate lint with a `ci/` allow-list (tests only) | landed, #487 |
| HAMCP-458 | Release tooling: refuse a guessed base, check the test venv, readiness sweep (tooling only) | landed, #486 |
| HAMCP-472 | `ha_search` domain-filtered wrong-empty result and always-partial warning | not landed |
| Native search | Design only; the build is planned for the next release | not landed |
| HAMCP-473 | `configuration_integrity_analysis` paging and filters | not landed |
| HAMCP-474 | Analysis coverage gaps | not landed |
| HAMCP-475 | `get_audit_log` paging with a cursor | not landed |
| HAMCP-476 | Per-automation run count over a time window | not landed |
| HAMCP-293 | Automation plan risk classified from the change | not landed |
| HAMCP-292 | Sensitive-data false positive on plan description wording | not landed |

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
2. **Item checks.** **Pending.** One read per landed item. The planned checks
   are:
   - a domain-filtered search returns entities;
   - integrity analysis pages beyond its first page;
   - analysis covers the three large automations;
   - the audit log pages;
   - run counts are returned.

   There are no lifecycle steps.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
