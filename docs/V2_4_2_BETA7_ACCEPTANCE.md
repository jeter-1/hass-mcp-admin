# Engineering 2.4.2-beta.7 acceptance

**Status: in progress.** This contract covers the 2.5.0 build, released as
2.4.2-beta.7. It follows 2.4.2-beta.6 (HAMCP-304, #373). Its content lands
through item pull requests into the `release/2.5.0` integration branch. The
final release pull request (HAMCP-331) completes every section marked
**pending** before it is offered for review.

See also the release notes:
[`V2_4_2_BETA7_RELEASE_NOTES.md`](V2_4_2_BETA7_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.7`, following 2.4.2-beta.6. The release
transition accepts only `2.4.2-beta.6 -> 2.4.2-beta.7` or
`2.4.2-beta.6 -> 2.4.2-rc.1` from the current base
(`scripts/release_transition.py`). It refuses `2.4.2-beta.6 -> 2.5.0-beta.1`.
"2.5.0" names the build, not the version string.

**Why the version is materialized at the branch base.** Every change under
`hass_mcp_engineering_beta/` needs a version transition
(`scripts/validate_addon_metadata.py`), and CI validates against `origin/main`
with a materialized, unstaged release whatever the pull request base
(`.github/workflows/ci.yml`). So 2.4.2-beta.7 is materialized once, in the
first commit on `release/2.5.0`, and item pull requests carry no version
change of their own.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed. At the base commit no
2.5.0 item has landed: the runtime is identical to 2.4.2-beta.6 apart from
the version strings.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## Items

Each item is reviewed in its own pull request into `release/2.5.0`. An item
that has not landed is not part of this release, and this contract makes no
claim about its behavior.

| Item | Scope | State |
| --- | --- | --- |
| HAMCP-327 (folds in HAMCP-301) | `control_helper` fails to ask: when helper safety evidence cannot be established it returns `confirmation_required` with the reason instead of dispatching | pending: not landed |
| HAMCP-288 | ha-mcp secret-path containment in Engineering's log reads | pending: not landed |
| HAMCP-319 Phase 2 | ha-mcp 9.1.0 admission side by side with the 8.5.0 surface, including the `/catalog/full` option | pending: not landed |
| HAMCP-290 | Bounded Core Marketplace inventory read | pending: not landed |
| HAMCP-278 / HAMCP-279 | Core registry signing and publication; automated Core release evidence | pending: status to be recorded |

## 1. Source and offline acceptance

**Pending.** The final release pull request binds, for each landed item:
- its owner decision and scope;
- its item pull request, reviewed head and review record;
- its required offline evidence, success and refusal paths, and negative
  reachability tests;
- a security section for each provider, write, workflow or persistence
  change.

It also binds:
- `2.4.2-beta.7` materialized, with exact document resolution;
- the final head SHA, its `validate` run and the single Evidence tier log
  named in the release pull request.

No item evidence is recorded yet. The Evidence tier runs once, on the final
release head.

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
   - `2.4.2-beta.7`;
   - the merged release SHA as build SHA;
   - `dirty: false`.
2. **Item checks.** **Pending.** One read-only check per landed item,
   written when the item lands.
3. **ha-mcp 9.1 `/catalog/full` option.** **Pending** (HAMCP-319 Phase 2).
4. **Tool catalog.** A fresh `tools/list` matches the catalog the final
   release pull request declares. **Pending.**

PASS needs every check. If a step fails, record the response in HAMCP-331.
Whether to roll back is Josh's decision.

## Rollback

**Pending.** The final release pull request states whether rolling back to
2.4.2-beta.6 is safe, from the storage, provider and option changes that
actually landed. No rollback claim is made yet.
