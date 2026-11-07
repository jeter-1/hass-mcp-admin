# HAMCP-267 Reference Store Consumers: Design Note (2.6.0 wave 1)

Status: design note only (HAMCP-332 wave 1). No runtime, test, schema, version or protected-path change.
Base: `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7). Citations are at that base,
relative to `hass_mcp_engineering_beta/ha_mcp_engineering/` unless a full path is given.

## 1. Register outcome and binding decisions

HAMCP-267 Outcome (register, quoted): "configuration_integrity_analysis, incident_correlation and handoff_generation
merge the diagnostic scene, group and dashboard reference store added by HAMCP-227 (with its truthful completeness),
so the integrity analysis named in HAMCP-227's outcome covers those sources; governance fingerprints and the helper
obligation ledger stay unaffected." Dependency: HAMCP-227 (Completed). Effort: Small.

Binding decisions:
- Josh, 2026-11-07 brief: HAMCP-267 is a 2.6.0 wave-1 feature; designs first.
- Josh, 2026-11-01 guidance (row note): HAMCP-227 merged the store into `entity_dependency_analysis` and
  `change_impact_analysis` only; this row carries the other three consumers.
- Josh's ranking, 2026-11-03 (row Next Action): feature lane position 2, start after HAMCP-277 merges.
- HAMCP-227 design (register, 2026-10-31 checkpoint 2): scene/group/dashboard are a diagnostic partition outside the
  governance-bound index. This note keeps that boundary; it adds no Home Assistant read.

Dependency state at the base: **HAMCP-277 has landed.** Merge `1008ef98` (#356) is an ancestor of `7246f2d2`
(`git merge-base --is-ancestor`). The register (read-only) records it Completed, deployed as beta.62 on 2026-11-03,
with the architect's live read-only acceptance reporting "Dashboards read: 6 of 6 listed". That is register-recorded
evidence, not observed here. HAMCP-270 (plan disclosure from the same store) also landed (#344, `13385ebc`).

## 2. Current state at base

- Store: `GlobalSourceDiagnostics` (`dependency/global_sources.py:89-130`) holds findings, four coverage rows, a
  partition fingerprint, `authority_current`, per-source providers and `bounded_obligations`. `visible()` (`:106-130`)
  withholds dashboard findings and makes the dashboard row `unavailable` when dashboard authority has retired. The
  module docstring says only the dependency and impact analyses merge it (`:1-7`). Bounds: 1,000 scenes, 1,000 groups,
  256 members, 10,000 membership findings per source type, 50 dashboards, 10,000 dashboard findings (`:36-47`).
- Build: every index build already collects it (`dependency/provider.py:1871-1884`, attached at `:1962`); the snapshot
  field is outside the obligation ledger and fingerprint (`dependency/models.py:557-560`, `:616-619`). Snapshot rows
  for scene/group/template/dashboard stay `unavailable` (proved by `tests/test_hamcp227_global_sources.py:916-926`).
- Membership findings carry `match_type="loaded_membership"` and the source's state (`global_sources.py:216-217`);
  dashboard findings carry the card path and no state (`:344-375`).
- How the two consumers merge it today: `entity_dependency_analysis` takes `visible()` once (`dependency/service.py:81-83`),
  appends its findings (`:126-129`), replaces the four rows with `merged_coverage` (`:137`, `:312-326`), adds its
  bounded terminals (`:197-206`), attributes providers per source (`:226-234`) and binds cursors to its fingerprint
  (`:86-89`). `change_impact_analysis` does the same (`impact/provider.py:352-363`, `:470-471`), keeps a partition row
  `unavailable` instead of `not_supported` when its provider is not `none` (`:756-763`), and binds its pagination
  snapshot to the visible partition fingerprint: a change before commit is `analysis_unavailable`, a change before a
  continuation is `stale_cursor` `diagnostic_evidence_changed`, and a failing identity fails closed
  (`impact/service.py:458-478`, `:608-618`, `:794-805`; tests `test_hamcp227_global_sources.py:1196-1254`).
- What is missing:
  - `configuration_integrity_analysis` reads `snapshot.findings` only (`integrity/provider.py:182`), adds only script
    bounded terminals (`:264-279`), and maps every snapshot row that is not complete/partial to `not_supported`
    (`:406-470`, line 447). Scene/group/dashboard are therefore always `not_supported`. Orphan candidates use only
    snapshot references as inbound evidence (`integrity/rules.py:248-262`).
  - `incident_correlation` scopes `snapshot.findings` only (`incident/provider.py:650-652`, used at `:276` and `:499`),
    derives `dependency_index` coverage from snapshot rows (`:606-647`), and reports its scoped
    `configuration_integrity` row as `complete` unconditionally (`:313-314`).
  - `handoff_generation` does the same from `index_snapshot.findings` and snapshot rows (`handoff/provider.py:194-232`).
  - None of the three binds its pagination snapshot to the partition (`integrity/service.py:414-433`, `:498-560`;
    `incident/service.py:268-271`, `:308-333`; `handoff/service.py:239-241`, `:285-311`).

## 3. Proposed change

Shared rule for all three: read `snapshot.global_sources.visible()` **once** per request (absent store = `None`) and
use that one object for findings, rows, terminals and identity, as the impact provider does. No new request, provider,
reader or refresh: the partition is the one the index build already read. `refresh_index` behaves as today.

**configuration_integrity_analysis** (`integrity/provider.py`, `integrity/rules.py`, `integrity/service.py`)
- References: requested-scope partition findings pass the same screen and sanitizer loop as snapshot findings, so a
  withheld identity counts as a sanitation failure and makes that source `partial`. Bound: a separate cap of 10,000
  partition references in sorted `evidence_id` order; overflow makes each source with dropped evidence `partial` with
  `integrity_partition_reference_limit: N`. The snapshot cap (`MAX_INDEX_REFERENCES`) is unchanged.
- Rows: scene, group and dashboard take the partition row. Mapping into the existing integrity values
  (`integrity/models.py:22-28`): `complete` -> `complete`; `partial` -> `partial`, keeping the partition warnings
  (including "Dashboards read: N of M listed" and counted reasons); `unavailable` -> `failed` with its reason (list
  failure, missing reader, withheld authority). Template stays `not_supported` with the HAMCP-264 deferral warning.
  Provider attribution per row from `provider_for` (`direct_ha_api` or `upstream_dashboard`), never `engineering`.
- Bounded sources: append `bounded_obligations` of the visible partition, as `dependency/service.py:197-206` does.
- Severity: partition references use `source_enabled=None`. A group's `on` or a scene's timestamp state is not
  consumer enablement, so `_source_enabled` (`integrity/rules.py:70`) must not raise a missing member to `high`.
  Membership evidence keeps its "loaded state attributes; stored configuration is not read" summary.
- Orphans: partition references count as inbound evidence. An entity referenced only by a scene, group or dashboard
  is no longer an orphan candidate; if it has no state it becomes a `registry_only_entity_reference` from that source.
  When any partition row in scope is not complete, the orphan block adds a warning naming those sources.
- Pagination: store the visible partition fingerprint in `_IntegritySnapshot`; check it before commit
  (`analysis_unavailable`, `index_changed_before_snapshot_commit`) and on continuation (`stale_cursor`,
  `diagnostic_evidence_changed`); a failing identity read fails closed. The provider gains
  `active_diagnostic_identity()` like `impact/provider.py:79-83`. The cursor wire format is unchanged.

**incident_correlation** (`incident/provider.py`, `incident/service.py`)
- `_scoped_dependency_findings` adds visible partition findings whose target is a focus/related entity (automation-id
  scoping does not apply to them). Events keep `source_type="dependency_index"`; the summary names the source type and,
  for membership, says "loaded membership" instead of "exact dependency".
- `_integrity_context` receives the same scoped partition references and the same `source_enabled=None` rule.
- `dependency_index` coverage: partition rows never add to `failed_items` (their counts are limitations, not read
  failures, per `source_coverage.normalize_coverage`). A non-complete scene/group/dashboard row makes the row `partial` with
  limitation `dependency_index_<source>_partial` or `dependency_index_<source>_unavailable` plus its warnings.
  Template keeps `dependency_index_unsupported_source_types`.
- Pagination: same partition binding as integrity, including when handoff passes its snapshot in.

**handoff_generation** (`handoff/provider.py`, `handoff/service.py`)
- Same scoping, coverage limitations and partition binding as incident. Partition items appear in
  `confirmed_findings` titled "Scene/group loaded membership" or "Dashboard reference". The existing
  `bounded_handoff_integrity_scope` integrity row is unchanged.

**Unchanged on purpose:** the snapshot, its fingerprint, findings, rows and obligation ledger; the public
`index.fingerprint` of all three outputs stays `snapshot.fingerprint` (their commit checks compare it with the active
index); governance plan hashes, consumer fingerprints and HAMCP-270 disclosure; `dashboard_integrity_analysis`
(a separate tool). Analysis identities (`incident_id`, `handoff_id`, evidence fingerprints) change when partition
evidence changes; they are analysis-local, not governance bindings.

## 4. Public schema / tool-registration impact

No new tool, parameter or output key; tool count unchanged; capability metadata (`capabilities.py:777-806`) unchanged.
Values change within existing vocabularies: integrity rows for scene/group/dashboard can be `complete`, `partial` or
`failed` instead of always `not_supported`; findings, events and handoff items can name `scene`, `group` or `dashboard`
sources (already in the integrity `source_types` input enum, `tools/analysis.py:303-314`); incident/handoff rows gain
new limitation IDs. Security-sensitive surfaces touched: analysis output, pagination cursor binding and sanitization.

## 5. Signing impact

None. No signed Core profile is new or changed: this design adds no Core command and no Home Assistant request. The
dashboard reads stay the existing HAMCP-227/277 build-time path under its existing lease and admission; scene/group
membership reuses states the build already reads. No fingerprint impact; nothing to batch.

## 6. Negative reachability and refusal tests

New file `tests/test_hamcp267_reference_store_consumers.py`, offline synthetic fixtures reusing the HAMCP-227 `Home`,
`FakeReader` and `build` helpers and the HAMCP-270 `bindings()` helper:
1. **Byte-unchanged governance (required negative test).** Build plain (`Home(scenes=False)`, no reader) and rich
   (`Home()` + dashboard) indexes; run all three analyses on each. Assert byte equality of: `snapshot.fingerprint`;
   `json.dumps([asdict(o) for o in snapshot.obligations], sort_keys=True)`; each `obligation_fingerprint`;
   `obligation_overflow_fingerprint`; `obligation_ledger_model`; snapshot rows for the four global types (still
   `unavailable`); each output's `index.fingerprint`. Also capture the same bytes before and after the three analyses
   on one snapshot (no mutation), and assert the HAMCP-270 `bindings()` of a helper-options, delete, metadata and
   availability plan targeting a partition-only entity are identical with and without the analyses having run.
2. Incomplete source is never silent absence: dashboard partial (custom card), dashboard budget exhausted, list
   failure, missing reader, withheld authority (`authority_current` false), scene `members_not_exposed`, registry
   incomplete, partition cap overflow. Each makes the matching row `partial`/`failed` (integrity) or adds the named
   limitation (incident, handoff) and the result `partial`; none reports the source `complete` or omits it.
3. Withheld authority removes dashboard findings from all three outputs (no cached evidence) and adds the warning.
4. Cursor binding: partition changed before commit, partition changed or authority retired before continuation, and a
   failing identity read each refuse (codes in section 3); an index refresh still refuses as today.
5. `source_types` filter: integrity with `["automation"]` shows no partition finding and its rows `not_requested`.
6. Severity: a missing member of an `on` group is `medium`, not `high`.
7. No new request: REST/WebSocket/dashboard-reader call counts for each analysis are equal for plain and rich builds
   on a cached index.
8. Mutation run (house practice for HAMCP-227/277): mutants that drop the partition, skip `visible()`, merge into
   `snapshot.findings`, extend `index.fingerprint`, append terminals to `snapshot.obligations`, or skip the cursor
   check must be killed by assertion.

## 7. Acceptance

- Success: on a synthetic home, integrity reports a missing member of a scene, an unavailable group member and a
  dashboard-only missing entity with exact paths and providers; incident and handoff show the scoped scene/group/
  dashboard relationships for a focus entity; a complete partition yields complete scene/group/dashboard rows.
- Refusal and failure preservation: tests 2-4 above; existing integrity, incident and handoff suites pass unchanged
  except assertions that encoded `not_supported` for scene/group/dashboard, which are updated deliberately.
- Verification: test 1 proves governance and ledger bytes unchanged. Recovery: a later refresh restores dashboard rows.
- Live read-only acceptance after a separately authorized deployment (architect): integrity with `refresh_index`
  shows partition rows and the "Dashboards read" warning. Not part of source acceptance.

## 8. Build slice and dependencies

One runtime PR (protected path, needs `-AuthorizedProtectedPath` for each file): `integrity/provider.py`,
`integrity/rules.py`, `integrity/service.py`, `incident/provider.py`, `incident/service.py`, `handoff/provider.py`,
`handoff/service.py`, an optional shared identity helper in `dependency/global_sources.py` (the module docstring
updated), the new test file, and docs: [configuration integrity](CONFIGURATION_INTEGRITY_ANALYSIS.md) (lines 66-71),
[incident correlation](INCIDENT_CORRELATION.md) (lines 89-90, 307-308), [handoff](HANDOFF_GENERATION.md),
[entity dependency analysis](ENTITY_DEPENDENCY_ANALYSIS.md) (lines 127-130), the read coverage matrix (RCG-093 text
still says live acceptance pending) and the [readiness map](FEATURE_READINESS_MAP.md). Version materialization follows
the orchestrator's 2.6.0 release plan. Dependencies: HAMCP-227, -270, -277 landed at the base. Completeness is still
limited by HAMCP-273 (large dashboards), HAMCP-264 (templates) and loaded-membership-only scenes (RCG-006); the design
reports those as partial. 2.5.0 overlap: open PRs #384-#387 touch none of these files (checked with `gh pr view`); the
HAMCP-290 work was not checked (no PR number given). Rollback: revert the PR; no persisted format changes.

## 9. Open questions — pending Josh

- Q1. `incident_correlation`'s scoped `configuration_integrity` row is `complete` unconditionally today
  (`incident/provider.py:313-314`). This design makes it inherit partiality only from the partition. Should it inherit
  every `dependency_index` partiality (for example, a partial automation source)? That is broader than this row's
  outcome, so the design takes no position.
