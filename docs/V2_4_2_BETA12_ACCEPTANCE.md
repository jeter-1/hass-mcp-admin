# Engineering 2.4.2-beta.12 acceptance

**Status: in progress.** This contract covers the 2.9.0.1 hotfix, released as
2.4.2-beta.12. It follows 2.4.2-beta.11 (2.9.0, #481). Its content lands
through item pull requests into the `release/2.9.0.1` integration branch. The
final release pull request completes every section marked **pending** before
it is offered for review.

See also the release notes:
[`V2_4_2_BETA12_RELEASE_NOTES.md`](V2_4_2_BETA12_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.12`, following 2.4.2-beta.11. The architect's
block of 2026-11-23 names this version for the 2.9.0.1 hotfix path, and the
value is authorized on that path only. "2.9.0.1" names the build, not the
version string.

**Why the version is materialized now.** `main` carries 2.4.2-beta.11 (#481),
and CI validates every pull request against `origin/main` with a
materialized, unstaged release whatever its base
(`scripts/validate_addon_metadata.py`, `.github/workflows/ci.yml`). So
2.4.2-beta.12 is materialized once on `release/2.9.0.1`, at its base (`main`
at 23c5c429), and item pull requests carry no version change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

## Items

Each item is reviewed in its own pull request into `release/2.9.0.1`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-470 | Lifecycle add-on identity read at governed apply on ha-mcp 9.2.0: decode bound, text comparison, error mapping and diagnostic | in progress on its branch; not landed; evidence pending |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds each landed item's pull
request, reviewed head, review record and offline evidence, and the final
head SHA, its `validate` run and the single Evidence tier log.

## 2. Release CI

The exact materialized final head's `validate` check passes, including the
release transition. **Pending.**

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. **Pending.**

## 4. Installed read-only acceptance (separately authorized, after deploy)

**Pending.**

## 5. Installed governed-change acceptance (separately authorized, after deploy)

**Pending.**

## Rollback

**Pending.** The final release pull request states the rollback target and
whether rolling back is safe.
