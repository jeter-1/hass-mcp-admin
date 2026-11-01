# Engineering 2.4.1-beta.55 acceptance

This contract covers HAMCP-227 Part 2: scene, group and dashboard reference
coverage in `entity_dependency_analysis` and `change_impact_analysis`. It is
read-only. It adds no tool, no input schema, no write path, no route and no
fallback.

See also:
- Release notes:
  [`V2_4_1_BETA55_RELEASE_NOTES.md`](V2_4_1_BETA55_RELEASE_NOTES.md).
- Coverage contract:
  [`ENTITY_DEPENDENCY_ANALYSIS.md`](ENTITY_DEPENDENCY_ANALYSIS.md).

**Release target.** `2.4.1-beta.55`, the next free beta after `main`'s
2.4.1-beta.54 (HAMCP-263, #339). It is staged in `.release/next-version` and
materialized on this branch by `python scripts/promote_next_release.py
--apply` (beta.54 → beta.55) in a separate commit that updates the three
version authorities:
- `hass_mcp_engineering_beta/config.yaml`;
- `ha_mcp_engineering/version.py` `SERVER_VERSION`;
- `scripts/validate_addon_metadata.py` `BETA_VERSION`.

HAMCP-263 (PR #339) merged first with beta.54, so this pull request merged
`main` and restaged to beta.55.

The contract separates four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed Engineering acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind the following:
- `2.4.1-beta.55` in all three version authorities,
  with exact acceptance and release-note resolution from
  `scripts/codex-context.py`;
- the PR head SHA;
- the passing PR `validate` run for that exact head, including
  `validate_source`, `validate_packaging` and `dashboard-disposable`;
- the Evidence tier log named in the PR.

The review needs a security section covering the following items:
- **No new write or dispatch reachability.** Scenes and groups use the
  states and entity registry the build already reads; they make no request.
  Dashboards use only the reviewed `list_dashboards` path and the reviewed
  analysis read (`get_analysis_configuration`). No other route, no direct
  Home Assistant fallback and no write.
- **Authority.** Every dashboard dispatch needs the dependency build's
  parent authorizer and a separately acquired
  `core.dashboard_configuration_read` lease. The lease is consumed once,
  revalidated before each dispatch, and finished or released on every exit.
  Without a parent authorizer nothing is acquired or sent. A route,
  observation or upstream authority drift after the list refuses each later
  read before dispatch.
- **Attribution.** Each dispatch is a child request with the parent's
  caller id and an audit event carrying `parent_request_id`, provider
  `upstream_dashboard`, the upstream operation, the result status and
  `fallback_occurred: false`. Scene and group rows say `direct_ha_api`;
  dashboard rows say `upstream_dashboard`; an unread row (templates, or
  dashboards with no reader) says `none` in both analyses.
- **Governance isolation.** The partition is outside the helper obligation
  ledger, the snapshot findings and the snapshot fingerprint. The snapshot
  coverage rows for scene, group, template and dashboard are unchanged; a
  test proves the fingerprint, findings, obligations and rows are equal
  with and without the partition. Cached dashboard evidence is withheld when
  its reviewed authority retires.
- **Policy.** More coverage never lowers the worst severity or the final
  assessment, and a partial or unavailable source never yields
  `no_known_impacts_with_complete_coverage`.
- **Untrusted text.** Source names and dashboard titles are bounded; unsafe
  path segments become `<key>`; rule ids are validated; secret-shaped
  members, identities and paths are withheld.
- **Bounds.** At most 1,000 scenes, 1,000 groups, 256 members per source,
  10,000 membership findings, 50 dashboards, 10,000 dashboard findings,
  1,024 walker items per dashboard and 1,000 bounded terminals; 4
  concurrent dashboard reads and a 6 s dashboard budget, run concurrently
  with the rest of the build. One 6 s deadline covers the dashboard
  session, the list and every read:
  no read starts after it, and work still in flight when it expires is
  cancelled and its lease released. An overrun counts
  `dashboard_scan_budget_exhausted` (named in `bounded_sources`) and one
  `dashboard_not_read` per unread dashboard; it is unavailable if the list
  never returned, otherwise partial. A document over the reviewed 2 MiB cap
  is refused (`dashboard_read_response_limit`, terminal
  `dashboard_document_limit_exceeded`) and its dashboard is partial. Each
  bound marks its source partial with a
  counted reason. `get_server_health` never builds the index.

Preserve the following:
- stable v1.1.2;
- every persisted format (the partition is process memory);
- every descriptor and input schema;
- static tools **79**; the catalog is **107 tools, 108 with the optional
  YAML reader admitted**;
- probe fingerprints, every signed Core capability profile and the signed
  Core registry (sequence 14);
- the signed ha-mcp registry and the ha-mcp contracts;
- add-on permissions and dependencies;
- `ci.yml` and every other workflow, all unchanged.

### 1.1 Completeness per source

| Source | `complete` only when | Otherwise |
|---|---|---|
| scene, group | every state was read, each source exposes a member list, the registry is complete, no bound was reached, no member or identity was withheld | partial with counted reasons (`*_members_not_exposed`, `*_state_unavailable` for a stateless group helper or registry-only scene, `entity_registry_incomplete`, `*_member_limit_exceeded`, `*_inventory_limit_exceeded`, `*_finding_limit_exceeded`, `*_member_invalid_or_withheld`, `*_identity_invalid_or_withheld`) |
| dashboard | the list was complete and untruncated, `lovelace` was listed, every dashboard was read and walked with no gap, no bound was reached and the budget held | partial with counted reasons (custom card or unsupported component, `strategy_not_expanded`, `dashboard_default_not_listed`, `dashboard_inventory_*`, `dashboard_read_*`, `dashboard_projection_limit_exceeded`, `dashboard_finding_limit_exceeded`, `dashboard_scan_budget_exhausted`, `dashboard_not_read`); unavailable with the reason when the list itself fails or no reader exists |
| template | never in this release | unavailable, deferred (HAMCP-264), with the template-platform registry count |
| script | unchanged | the row also states that complete enumeration is deferred (HAMCP-264) |

When the bounded index drops evidence, every source type with dropped
evidence becomes partial, not only automations.

### 1.2 Offline proof

No test reaches a live system. Every fixture is synthetic and live-shaped
(`scene.synthetic_evening`, `group.synthetic_downstairs`, dashboards
`lovelace` and `synthetic-two`).

- `tests/test_hamcp227_global_sources.py` (71 tests):
  - scene and group references at `$.attributes.entity_id[n]`, and every
    completeness state, with exact-boundary controls for each bound;
  - dashboard references at exact paths through conditional, stack and grid
    cards; custom card, dashboard strategy and view strategy partial; a
    read failure isolated to its dashboard; missing `lovelace`; truncated or
    partial list; budget overrun; concurrency bound; projection and finding
    bounds; no reader; list failure; retired authority;
  - the dashboard budget on a simulated clock (R1-M1): a session that
    consumes the budget, a list that never
    returns, a list that consumes the budget, an overrun mid-list, reads in
    flight at the deadline, within budget, an oversize document through the
    reviewed size cap, zero reads after the deadline, the lease released,
    and scene, group and automation evidence preserved;
  - impact pagination (R1-M2): consistent pages with no extra read, stale
    cursor after authority retirement, a replaced partition and an index
    refresh, refusal when the partition changes before commit, a failing
    identity fails closed (also when no partition was stored), and the
    counters;
  - the reader: exactly 1 list + N reads, the lease lifecycle, refusals
    with zero dispatch, drift after the list, caller and audit attribution,
    screened paths;
  - the build: 0 extra requests for scenes and groups; 1 dashboard list +
    N reads in the build profile; the snapshot fingerprint and rows
    unchanged; index truncation marks dropped sources partial; health never
    builds;
  - both consumers show scene-, group- and dashboard-only references; the
    `source_types` filter excludes them; template deferral and provider
    `none`;
  - policy: no downgrade, and partial dashboard coverage stays limited;
  - the bound table names every partition limit.
- Mutations in a copied tree (`docs/evidence/hamcp227-mutations/`): round 1
  killed 48 of 55; the 7 survivors each got an isolated test; round 2
  killed 55 of 55; M56 (provider attribution) is killed. Round 3 replays
  M01–M56 and adds M57–M70 for the R1 budget and cursor enforcement
  points: 70 of 70 killed, both in the full module and by each mutant's
  isolated target test, after one survivor (M68) got its own test.

### 1.3 Review item: governed rename or removal disclosure

Question: when a governed rename or removal plan is disclosed before
approval, does it surface the new dashboard, scene and group references as
information, without fingerprinting them?

**Answer: no.**
- Consumer disclosure (`_consumer_evidence` in
  `governance/entity_availability_exposure_planning.py`) reads only the
  snapshot findings, never the partition, and its output feeds
  `consumer_fingerprint`, which the plan binds. The delete and helper
  options planners bind their consumer evidence the same way.
- No rename planner exists: `new_entity_id` is refused by the entity
  metadata tools.
- The entity metadata disclosure keeps its fixed
  `dashboards_scripts_and_templates_not_scanned` statement.

So no approval binds to dashboard evidence, and a dashboard edit cannot
re-trigger approval. A follow-up would need:
1. a separate `global_source_consumers` key built from
   `global_sources.visible().findings` for the target;
2. that key excluded from `consumer_fingerprint` and every plan binding;
3. projection as a sibling of the fingerprinted consumer items in the
   availability/exposure and delete disclosures;
4. statement lines and updated coverage warnings stating that these
   references are informational, loaded membership for scenes and groups,
   and possibly partial;
5. a test that adding or changing global findings leaves every plan
   fingerprint and approval hash unchanged.

### Downgrade rule

Rollback is reinstalling **2.4.1-beta.54**. beta.55 writes no new
persisted record. Never
clear F3 journals, task files or locks.

## 2. Release CI and publication

Require exact-head CI before release acceptance. Pending or unavailable
jobs are not passing evidence.

Publication needs Josh's Ready authorization and the protected merge
handoff. Verify the merged source, the release tag, the immutable version
and commit images, and provenance and SBOM with the shipped verifiers.
Never invent future image identities.

## 3. Installed Engineering (read-only, after deploy)

Installation needs separate authorization. Every step is read-only.

1. **Installed-image binding.** Bind the running add-on to the published
   image index digest, the release tag and the protected `main` merge
   commit.
2. **Catalog.** `tools/list` shows 107 tools, or 108 with the YAML reader
   admitted.
3. **Coverage.** `entity_dependency_analysis` for `<architect-selected
   target entity>` with `refresh_index: true` (the target is recorded in the
   private acceptance handoff, not in this repository):
   - the scene, group and dashboard rows are no longer `unavailable`
     (`complete` or `partial` with counted reasons; dashboards are expected
     to be partial on a Core version the walker does not pin);
   - the template row is `unavailable` with the HAMCP-264 reason and the
     template-platform count.
4. **Timing.** The rebuild in step 3 completes in ≤ 15 s (build profile).
   A warm `get_server_health(check_ha=false)` completes in ≤ 1.5 s and does
   not build the index.
5. **Consumer.** `change_impact_analysis` (`operation: remove_entity`) for
   an entity that a dashboard or scene references and no automation or
   script does shows that dashboard or scene as an affected object, and the
   assessment is not `no_known_impacts_with_complete_coverage`.
6. **Smoke and regression.** Run the standard read-only smoke and
   regression checks.

## 4. Out of scope

This document does not authorize:
- template and complete script enumeration (HAMCP-264);
- the integrity, incident and handoff analyses (HAMCP-267);
- the "unclassified" bounds (HAMCP-268);
- governed plan disclosure of the partition (section 1.3);
- any write path, signing or admission change, or `ci.yml` change;
- any downgrade.
