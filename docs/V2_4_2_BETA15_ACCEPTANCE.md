# Engineering 2.4.2-beta.15 acceptance

**Status: in progress.** This contract covers the 2.11.0 build, released as
2.4.2-beta.15. It follows 2.4.2-beta.14 (the 2.10.0 build, HAMCP-500, #522).
Its content lands through item pull requests into the `release/2.11.0`
integration branch. The final release pull request completes every section
marked **pending** before it is offered for review.

See also the release notes:
[`V2_4_2_BETA15_RELEASE_NOTES.md`](V2_4_2_BETA15_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.15`, following 2.4.2-beta.14. Josh approved
the 2.11.0 plan on 2026-11-25, including this version, and the kickoff on
2026-11-27; the value is authorized on the 2.11.0 release path only.
"2.11.0" names the build, not the version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.14 (#522),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.15 is materialized once on `release/2.11.0`, created from `main`
at 54627e35, and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

**Merge provenance.** HAMCP-521 changes the merge provenance check and lands
on `main` in its own pull request before the 2.11.0 release pull request.
`release/2.11.0` then merges `main`.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.11.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-425, HAMCP-432 | List paging contract: list cursor and bare-object lists | not landed |
| HAMCP-492 | Integrity compact page budget of 24000 bytes | not landed |
| HAMCP-359 | Reconciliation audit record within its bound | not landed |
| HAMCP-511, HAMCP-527, HAMCP-513, HAMCP-517 | Native history slices, paging, concurrency, refusal naming and request count | not landed |
| HAMCP-273, HAMCP-291 | Large dashboard read-back | not landed |
| HAMCP-444 | Dashboard analysis rule profile | not landed |
| HAMCP-477 | Dashboard-write sensitive-data guard | not landed |
| HAMCP-522, HAMCP-523, HAMCP-525, HAMCP-520 | Native search follow-ups | not landed |
| HAMCP-447 | Native search dashboard and helper identities | not landed |
| HAMCP-296, HAMCP-431, HAMCP-389, HAMCP-524 | Trace watch lifecycle | not landed |
| HAMCP-510 | Fixed gateway readers without a parent request | not landed |
| HAMCP-471 | JSON exponent overflow refused as non-finite | not landed |
| HAMCP-321, HAMCP-495 | Reliability condition-stop step and batch partial reasons | not landed |
| HAMCP-512 | `ha_eval_template` completeness | not landed |
| HAMCP-514, HAMCP-515 | Provider naming and collision reporting | not landed |
| HAMCP-518 | `get_audit_log` per-tool summary | not landed |
| HAMCP-378 | Created-object rollback plan form | not landed |
| HAMCP-394, HAMCP-429, HAMCP-486 | Provable risk elevation | not landed |
| HAMCP-445 | Scene list rows | not landed |
| HAMCP-519 | Input helper stored settings read | not landed |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow, public-schema or
  persistence change.

It also binds:
- `2.4.2-beta.15` materialized, with exact document resolution;
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
   - `2.4.2-beta.15`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** **Pending.** One read per landed item, named by the
   final release pull request from the landed source.

   Lifecycle steps, if any landed item needs one, are named there too.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe, from the storage, provider and option changes
that actually landed.
