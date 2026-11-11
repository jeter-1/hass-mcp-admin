# Engineering 2.4.2-beta.8 acceptance

**Status: in progress.** This contract covers the 2.6.0 build, released as
2.4.2-beta.8. It follows 2.4.2-beta.7 (HAMCP-331, #383). Its content lands
through item pull requests into the `release/2.6.0` integration branch. The
final release pull request completes every section marked **pending** before
it is offered for review.

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
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.6.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior. Landed items are listed by their pull request; the
final release pull request binds their evidence.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-149 | Offline approval-completion fixture (tests and docs only) | landed (#396); evidence pending |
| HAMCP-104 Slice A | Precise stale-state pre-dispatch refusal reasons | landed (#398); evidence pending |
| HAMCP-294 | Bounded logbook window and entity/name filter | landed (#397); evidence pending |
| HAMCP-267 | Integrity, incident and handoff consume the scene/group/dashboard reference store | landed (#400); evidence pending |
| HAMCP-296 | Trace watch lifecycle | landed (#401); evidence pending |
| HAMCP-229 | Bounded automation reliability batch analysis | landed (#402); evidence pending |
| HAMCP-143 | Bounded refusal diagnostics for dashboard compiler limits | landed (#412); evidence pending |
| HAMCP-337 / HAMCP-338 | Entities-card row classification by domain; E031 lock-tile risk fix | landed (#411); evidence pending |

The 2.5.0 build reached `release/2.6.0` through #410 (`release/2.5.0`) and
the HAMCP-354 catch-up with `main` through #413.

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.8` materialized, with exact document resolution;
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
   - `2.4.2-beta.8`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** **Pending.** One read-only check per landed item.
3. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in the release
task. Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states whether rolling back to
2.4.2-beta.7 is safe, from the storage, provider and option changes that
actually landed. No rollback claim is made yet.
