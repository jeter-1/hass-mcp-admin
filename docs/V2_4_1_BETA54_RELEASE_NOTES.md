# Engineering 2.4.1-beta.54 release notes

Engineering 2.4.1-beta.54 adds scene, group and dashboard references to
`entity_dependency_analysis` and `change_impact_analysis` (HAMCP-227 Part
2). It is read-only. It adds no tool, input schema, write path, route,
fallback or plan family. It changes no signed registry, signed profile,
snapshot fingerprint or governance-bound evidence.

**Version sequencing.** This builds on 2.4.1-beta.53 (HAMCP-071) and
materializes beta.54, the next free beta, with
`scripts/promote_next_release.py --apply`. HAMCP-263 (PR #339) also targets
beta.54; whichever pull request merges first keeps it, and the other
restages to the next free beta.

Acceptance: [`V2_4_1_BETA54_ACCEPTANCE.md`](V2_4_1_BETA54_ACCEPTANCE.md).
Coverage contract:
[`ENTITY_DEPENDENCY_ANALYSIS.md`](ENTITY_DEPENDENCY_ANALYSIS.md).

## Global reference coverage (HAMCP-227 Part 2)

- **Separate diagnostic partition.** Scene, group, template and dashboard
  evidence lives in `GlobalSourceDiagnostics`, like the script partition.
  It is outside the helper obligation ledger, the snapshot findings and the
  snapshot fingerprint. Governance cannot be downgraded by it, and a
  dashboard edit does not change an approval-bound fingerprint. The snapshot
  coverage rows for these four sources stay as they were; only
  `entity_dependency_analysis` and `change_impact_analysis` overlay the
  partition rows. The integrity, incident and handoff analyses do not yet
  merge it (HAMCP-267).
- **Scenes and groups.** References come from the `entity_id` attribute of
  the states the build already reads, at `$.attributes.entity_id[n]`. No
  extra request is made. Provider `direct_ha_api`; the evidence is labeled
  "loaded membership (state attributes), not stored configuration". A
  scene or group without a member list, a stateless group helper (registry
  only), an incomplete entity registry, and any member, inventory or
  finding bound are partial, with counted reasons.
- **Dashboards.** The reviewed `list_dashboards` path enumerates
  dashboards, the reviewed analysis read path
  (`get_analysis_configuration`) reads each one, and the existing dashboard
  walker extracts references through conditional, stack and grid nesting.
  Provider `upstream_dashboard`, no fallback. Each dispatch is gated on the
  build's authority and a separately acquired
  `core.dashboard_configuration_read` lease, and each writes an attributed
  audit event with the parent request id. One list request plus one read
  per dashboard, at most 4 at a time, within a 6 s budget, running
  concurrently with the rest of the build. A custom card, a strategy, a
  missing `lovelace` entry, an unsupported component, a truncated list or
  projection, a read failure and a budget overrun are partial. A read
  failure stays with its dashboard. Cached dashboard evidence is withheld
  when the reviewed authority it was read under retires.
  One 6 s deadline covers the dashboard session, the list and every read:
  no read starts after it, and work still in flight when it expires is
  cancelled and its lease released. An overrun counts
  `dashboard_scan_budget_exhausted` (named in `bounded_sources`) and one
  `dashboard_not_read` per unread dashboard; it is unavailable if the list
  never returned, otherwise partial. A document over the reviewed 2 MiB cap
  is refused (`dashboard_read_response_limit`, terminal
  `dashboard_document_limit_exceeded`) and its dashboard is partial.
  `change_impact_analysis` cursors are bound to the visible diagnostic
  partition as well as the index: when dashboard authority retires or the
  partition changes, a continuation returns `stale_cursor`
  (`diagnostic_evidence_changed`) and never serves cached pages.
- **Templates and complete script enumeration are deferred (HAMCP-264).**
  Template coverage is `unavailable` with the reason and the count of
  template-platform registry entries (a lower bound). The script row states
  that complete script enumeration is deferred.
- **Truncation.** When the bounded index drops evidence, every source type
  whose evidence was dropped is partial, not only automations.
- **Bounds.** At most 1,000 scenes, 1,000 groups, 256 members each and
  10,000 membership findings; at most 50 dashboards, 10,000 dashboard
  findings and 1,024 walker items per dashboard; 1,000 bounded terminals.
  Each bound is listed in the bound table and marks its source partial.
- **Policy.** More coverage never downgrades risk. A partial or unavailable
  source keeps `final_assessment` away from
  `no_known_impacts_with_complete_coverage`, and a partition row that is
  read but unavailable stays `unavailable` in the impact matrix instead of
  becoming `not_supported`.
- **Attribution.** A partition row names the provider that read it. A row
  nothing read (templates, or dashboards with no reader) says `none`;
  before, the impact matrix attributed it to `direct_ha_api`.
- **Untrusted text.** Source names are bounded, dashboard titles are
  bounded, path segments that are not safe identifiers become `<key>`,
  rule ids are validated, and secret-shaped members, identities and paths
  are withheld.
- **Health.** `get_server_health` still never builds the index. The build
  profile counts `dashboard_list` and `dashboard_configuration` requests.

## Governed plans (review item)

Governed plans do **not** yet surface the new references. Consumer
disclosure in the entity availability, exposure, delete and helper options
plans reads only the snapshot findings, and that output is fingerprinted.
There is no rename planner. The entity metadata disclosure still states
that dashboards, scripts and templates are not scanned. This is unchanged,
and it is the safe direction: no approval binds to dashboard evidence. A
follow-up would add a separate unfingerprinted disclosure key; see the
acceptance contract, section 1.3.

## Counts

- Static tools: 79 (unchanged).
- Advertised catalog: 107, or 108 with the optional YAML reader admitted
  (unchanged).

## Rollback

Reinstall 2.4.1-beta.53. beta.54 writes no new persisted record; the partition is process memory.
