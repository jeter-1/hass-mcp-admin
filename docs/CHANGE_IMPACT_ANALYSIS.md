# Change-impact analysis

`change_impact_analysis` is the read-only Engineering-native tool introduced in
Beta 15 and contract-hardened in Beta 16. It answers
answering a narrow pre-change question: before one Home Assistant entity is
renamed, removed, or disabled, which known objects and behaviors could be
affected?

It is an evidence facilitator, not an executor. It never changes an entity,
calls a service, writes configuration, creates a change plan, requests approval,
reloads, or restarts Home Assistant. It is also not global orphan detection or
incident correlation.

## Contract and routing

The lifecycle is `beta_native`, routing is `engineering_native`, the selected
provider is `engineering`, and the policy is
`single_entity_change_impact_read`. Fallback is prohibited and Standard Home
Assistant MCP has no exact approved mapping for this capability.

Inputs are:

| Field | Contract |
| --- | --- |
| `entity_id` | Required canonical lowercase Home Assistant entity ID. |
| `operation` | Required: `rename_entity`, `remove_entity`, or `disable_entity`. |
| `replacement_entity_id` | Required only for rename; must be canonical, unused, and different from the source. |
| `include_indirect` | Boolean, default `true`. |
| `max_depth` | Integer 1-3, default 2; bounds explicit indirect traversal. |
| `source_types` | Bounded subset of automation, blueprint, script, scene, group, template, and dashboard. |
| `detail_level` | `summary`, `standard`, or `evidence`; default `standard`. |
| `limit` | Integer 1-100, default 20; effective payload caps also apply. |
| `cursor` | Opaque signed pagination cursor. |
| `refresh_index` | Boolean, default `false`. |

Validation occurs before any provider or Home Assistant request. Paths, URLs,
whitespace, control characters, uppercase identifiers, malformed IDs, unsupported
operations, and invalid replacement combinations return `invalid_request` with
stable `details.field`, `details.reason`, and `details.operation` values,
`request_validation`, zero Home Assistant requests, zero Home Assistant duration,
and `upstream_attempted: false`. Validation also performs no dependency-index
lookup or build and creates no pagination snapshot. A canonical source absent from both the
state machine and entity registry returns `entity_not_found` once.

## Operations

- Rename reports destination conflicts and consumers whose references need
  review or migration. It never assumes Home Assistant rewrites a reference.
- Remove reports consumers that could retain stale or missing dependencies.
  Current unavailability is not treated as proof that removal is safe.
- Disable reports unavailable, unknown, absent-state, stopped-trigger, and
  changed-condition risks without treating disablement as deletion.

The remediation checklist is inert advisory text. A later change must use the
separate governed lifecycle.

## Evidence and source coverage

The provider uses the existing dependency index; it does not build a second
graph. It combines exact state and entity-registry reads, static index findings,
bounded indirect traversal, exact registry relationships, recent trace headers,
and sanitized exact System Log correlations.

| Source | Current Beta 16 coverage | Assessment role |
| --- | --- | --- |
| Exact source state | Complete exact entity read, or explicit missing/unavailable failure | Required |
| Entity registry | Complete registry enumeration with only the exact target retained | Required |
| Automation configuration | Existing dependency-index coverage | Required when requested |
| Blueprint input/source | Existing dependency-index coverage | Required when requested |
| Script, scene, group, template, dashboard configuration | `not_supported` unless the shared index has honest supported coverage | Required when requested; blocks a complete clean result |
| Device relationship | Exact entity-registry link only; no full device record | Optional, partial |
| Area relationship | Exact entity-registry link only; no full area record | Optional, partial |
| Recent automation traces | Bounded retained trace headers for affected automations | Optional; retention is bounded |
| System Log | Bounded sanitized in-memory exact-ID matches | Optional, partial; retention unknown |
| Static YAML/packages and custom integrations | Not inspected | Explicit limitation |

Every requested or relevant source reports `complete`, `partial`, `unavailable`,
`not_requested`, or `not_supported`. Optional runtime evidence cannot upgrade
incomplete static coverage. A source failure cannot become a complete clean
assessment.

## Deterministic rule catalog

Rules are emitted only when their evidence exists:

1. `direct_automation_reference`
2. `direct_blueprint_reference`
3. `direct_script_reference`
4. `direct_scene_reference`
5. `direct_group_reference`
6. `direct_template_reference`
7. `direct_dashboard_reference`
8. `entity_registry_relationship`
9. `device_registry_relationship`
10. `area_relationship`
11. `indirect_dependency_path`
12. `unresolved_dynamic_reference`
13. `recent_trace_reference`
14. `correlated_system_log_reference`
15. `rename_destination_conflict`
16. `rename_reference_migration_required`
17. `remove_orphaned_consumer`
18. `disable_runtime_availability_risk`
19. `source_coverage_incomplete`
20. `target_currently_unavailable`
21. `target_registry_disabled`
22. `target_missing_from_state_machine`

Every finding has a stable ID, rule, severity, confidence, impact type, stable
affected-object identifier, direct/indirect flag, depth, bounded explanation and
consequence, real evidence references, review/remediation flags, and relevant
coverage. Findings are ordered deterministically. Repeated references are grouped
by affected object and operation consequence, while their individual paths remain
evidence; five references in one automation do not become five root causes.

Generated explanations use grammatical source-specific articles (for example,
“An automation”). Wording changes do not change stable rule IDs.

## Assessment semantics

Exactly one assessment is returned:

- `blocking_impacts_found`: an existing rename destination or another explicit
  blocking condition was confirmed.
- `review_required`: known impacts or material uncertainty require review.
- `no_known_impacts_with_complete_coverage`: no finding exists and every required
  requested source completed.
- `no_known_impacts_with_incomplete_coverage`: no known impact exists, but required
  evidence is partial, unavailable, unsupported, truncated, or unresolved.

The complete clean result is intentionally difficult to earn. No result claims an
entity is safe merely because a relationship was not found.

## Output, bounds, and pagination

The facilitator envelope includes lifecycle/routing/provider/policy, request ID,
no-fallback state, coverage, timing, and terminal success/partial/failure state.
Data includes the target summary, operation, replacement, one UTC analysis
timestamp, assessment, counts, findings, groups, advisory checklist, evidence
references, coverage matrix, dynamic summary, pagination, index provenance,
timing, and explicit limitations.

Summary omits evidence payloads. Standard includes bounded references without raw
bulk content. Evidence adds bounded sanitized snippets and paths. The public schema
accepts limits through 100, while response-size caps are 50 summary findings, 30
standard findings, and 20 evidence findings per page. Pagination reports
`requested_limit`, `effective_limit`, `maximum_limit`, `effective_payload_cap`,
`clamped`, and `clamp_reason`; clamping is never silent.

Whole-analysis counters use these non-overlapping names:

| Field | Meaning |
| --- | --- |
| `finding_count` | Number of deterministic rule findings in the complete analysis, independent of the current page. |
| `direct_finding_count` / `indirect_finding_count` | Findings split by confirmed direct designation; together they equal `finding_count`. |
| `findings_by_severity` | Whole-analysis finding totals whose sum equals `finding_count`. |
| `findings_by_object_type` | Finding observations by affected-object type; repeated objects may contribute more than once. |
| `unique_affected_object_count` | Unique `(object type, stable object identifier)` pairs in the analysis. |
| `unique_affected_objects_by_type` | Unique identifiers per type; its sum equals `unique_affected_object_count`. |
| `unique_root_cause_count` | Distinct affected-object/consequence groups produced by deterministic grouping. |

The Beta 15 fields `severity_totals`, `direct_impact_count`,
`indirect_impact_count`, `affected_object_count`, and `affected_object_totals`
remain as deprecated aliases. They now carry the corrected semantics shown in
`counter_semantics`; in particular, no field named for affected objects contains
finding counts.

`dynamic_reference_summary` separates references confirmed to be in an already
target-related object, unresolved references within requested source types, and
global unresolved references outside the request. Requested-scope uncertainty is
reported with source type, stable source ID, and configuration path where
available, marks its source coverage partial, and requires manual review.
Out-of-scope references remain visible as a count but do not create a false review
requirement. Confirmed static references remain distinct from dynamic uncertainty.

Cursors are HMAC-signed and bound to the source, operation, replacement, indirect
setting, depth, sources, detail level, committed index generation/fingerprint,
evidence fingerprint, coverage state, and fixed analysis timestamp. `refresh_index`
is a first-page collection instruction, not a result-shaping query identity: a
refreshed first page may therefore continue with the required
`refresh_index=false`. The snapshot is created only after the refreshed index is
the active committed generation. Continuation checks that active identity without
performing an index lookup, rebuild, or Home Assistant request and reads the next
page only from the bounded sanitized process-local snapshot.

Snapshots live for at most five minutes. A truly replaced or invalidated index,
changed query, missing snapshot, or expiration returns `stale_cursor`; a modified
or malformed cursor returns `invalid_cursor`. Cursor validation is fail-closed.
This snapshot is pagination state, not a general result cache.

## Timing and cache provenance

Timing separates the current request wall clock, Engineering analysis wall clock,
dependency-index lookup, current index build, original cached build provenance,
evidence collection, cumulative Home Assistant attempt effort, upstream wall-clock
span, request count, and maximum concurrency. A cache hit never reports the old
index build as work repeated by the current request.

## Security, audit, and telemetry

All Home Assistant state, registry, dependency, trace, log, friendly-name, URL,
exception, and unknown content crosses the centralized recursive sanitizer before
selection, correlation, hashing, truncation, formatting, or serialization. It is
inert untrusted evidence. Tokens, cookies, passwords, webhooks, auth-flow values,
Matter material, credential URLs, sessions, and sanitation failures use stable
category-only markers. Sanitation fails closed; raw content is never a fallback.

Audit records may retain the tool, request ID, bounded validated arguments, source
and replacement IDs, operation, terminal outcome, counts, coverage state, and
timing. They exclude state values, configurations, trace/log/template/dashboard
content, findings, evidence summaries, dependency paths, error text, cursors, and
secret-derived material.

Health telemetry is cumulative and identity-free. `request_count` includes new
analyses and cursor pages. Terminal success/partial/failure and whole-analysis
aggregates count new analyses only. Health exposes `finding_count`,
`findings_by_object_type`, direct/indirect finding counts, per-analysis unique
affected-object totals, unique root-cause totals, dynamic-review events,
requested-scope unresolved dynamic-reference totals, source failures, truncation,
index cache state, and cursor-specific events. Cursor pages never add the same
analysis aggregates again; cursor failures increment cursor counters and are not
misreported as failed new analyses. Corrected deprecated health aliases are listed
under `counter_semantics`.

## Troubleshooting

- `entity_not_found`: confirm the canonical ID exists in state or registry.
- `invalid_request`: correct the ID/operation/replacement locally; no upstream call
  was made.
- `analysis_unavailable` or `provider_timeout`: inspect health, source coverage, and
  sanitized System Log; do not infer a clean result.
- `stale_cursor`: rerun the first page. The snapshot expired, its query binding
  changed, or the committed dependency index was replaced or invalidated.
- `invalid_cursor`: do not edit or decode/re-encode the opaque cursor.
- `not_supported` coverage: narrow `source_types` only if that matches the decision
  being evaluated; never reinterpret unsupported as clear.
- Missing tool after upgrading from Beta 14: refresh or recreate only the beta
  connector because Beta 15 changed the manifest from 34 to 35 tools. Beta 16
  does not change the manifest or any input schema.

## Beta 16 read-only live acceptance

Do not run this procedure from CI or against production.

1. Call `server_info(check_ha=false)`.
2. Confirm `2.0.0-beta.16`, 35 tools, and 25 canonical tools.
3. Call `list_capabilities`.
4. Confirm `change_impact_analysis` is additive, beta-native, Engineering-routed, and read-only.
5. Capture health and provider counters.
6. Select a known entity referenced by at least one automation.
7. Run `remove_entity` analysis with standard detail.
8. Confirm the known consumer appears with cited evidence.
9. Run `disable_entity` for the same entity.
10. Confirm its consequences differ appropriately from removal.
11. Run `rename_entity` with a valid unused destination.
12. Confirm migration-required findings.
13. Run rename with an existing destination.
14. Confirm a non-destructive destination conflict.
15. Analyze a valid nonexistent target and confirm `entity_not_found`.
16. Analyze `../config` and confirm `invalid_request`, zero HA time/requests, and no upstream attempt.
17. Exercise indirect traversal and depth bounds.
18. Request a low-limit first page with `refresh_index=true` and enough findings for pagination.
19. Immediately continue with the returned cursor and `refresh_index=false`; confirm success, the same analysis timestamp, and the next findings.
20. Continue through another page and confirm no Home Assistant request or index build occurred on either continuation.
21. Change a result-shaping input and confirm `stale_cursor`; alter the cursor and confirm `invalid_cursor`.
22. Refresh or invalidate the shared index after issuing a cursor and confirm the old cursor becomes stale.
23. Confirm finding, severity, direct/indirect, unique-object, and root-cause totals reconcile and remain unchanged on every cursor page.
24. Confirm health request/continuation counters advance while terminal and aggregate counters advance only for the first page.
25. Confirm unresolved dynamic references in requested source scope identify their source/path and require manual review.
26. Confirm unresolved references only in unrequested source types do not create a false review requirement.
27. Exercise every invalid operation/replacement combination and confirm field/reason details, zero HA activity, and no index or snapshot activity.
28. Confirm incomplete coverage cannot produce a complete clean assessment.
29. Confirm current timing and index-build provenance are separate and truthful.
30. Confirm audit excludes findings, evidence, cursors, secrets, and authenticated paths.
31. Confirm health contains no entity identities.
32. Call `get_error_log(tail_lines=50)` and confirm sanitization remains intact.
33. Confirm no Standard MCP success or fallback is claimed.
34. Confirm no writes, services, plans, approvals, reloads, or restarts occurred.
