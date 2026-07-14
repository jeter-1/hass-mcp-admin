# Incident correlation

`incident_correlation` is the Engineering-native, read-only capability introduced
in Beta 19 and coverage-corrected in Beta 20 for
correlating bounded Home Assistant evidence around one entity, one internal
automation ID, or both. It produces ranked hypotheses with supporting,
contradicting, missing, and coverage-limited evidence. Correlation is not proof of
causation, and temporal proximity alone is insufficient.

Policy: `bounded_incident_correlation_read`. Provider: `engineering`. Fallback:
none. The tool performs no service call, remediation, configuration write,
governance operation, reload, restart, subscription, alert, or background
monitoring. It analyzes one bounded request at a time.

Beta 23 provider accounting treats `engineering` as selected metadata until the
incident evidence provider is dispatched. Input/cursor validation and frozen
snapshot continuation perform no provider work and cannot increment provider
requests or failures. Successful partial coverage remains a non-failing provider
operation; actual source failures and provider failures retain separate counters.

## Public schema

```json
{
  "focus_entity_id": "",
  "automation_id": "",
  "related_entity_ids": [],
  "lookback_hours": 24,
  "correlation_window_minutes": 10,
  "trace_limit": 10,
  "include_dependency_context": true,
  "include_integrity_context": true,
  "include_reliability_context": true,
  "detail_level": "standard",
  "limit": 20,
  "cursor": "",
  "refresh_index": false
}
```

`focus_entity_id` is a canonical entity ID. `automation_id` is the internal ID
returned by `list_automations`, not necessarily `automation.example`. At least one
must be present on every request. Up to 20 distinct related canonical entity IDs
are allowed; the focus entity cannot be repeated. Lookback is 1–168 hours,
correlation window 1–60 minutes, trace limit 1–50, and page limit 1–100. Detail is
`summary`, `standard`, or `evidence`. Cursor requests reject
`refresh_index=true`.

## Evidence and source coverage

The Engineering provider can compose these bounded sources:

| Source | Provider | Bound and semantics |
| --- | --- | --- |
| Current state | Direct HA API | One sanitized complete state inventory |
| Entity registry | Direct HA API | One sanitized complete registry inventory |
| Entity history | Direct HA API | Selected entity IDs, requested lookback |
| Logbook | Direct HA API | Selected entity IDs, requested lookback |
| Automation config | Direct HA API | One internal automation ID |
| Automation traces | Direct HA API | At most `trace_limit` runs in the lookback |
| System Log | Direct HA API | At most 200 sanitized structured entries; retention is unknown |
| Dependency index | Engineering | One shared committed generation when requested |
| Configuration integrity | Engineering | Material scoped Beta 18 findings only |
| Automation reliability | Engineering | Material findings from the shared internal reliability engine |

Every source reports `complete`, `partial`, `failed`, `not_supported`, or
`not_requested`, plus provider, capability, item counts, warnings, duration,
cached provenance, failure category, and whether upstream access occurred.
Failures are not hidden. Approximate Standard HA MCP delegation is not claimed.

### Beta 20 coverage semantics

Coverage state and failure state are separate:

| Evidence state | Meaning | Failure category |
| --- | --- | --- |
| Complete | Requested supported scope was inspected successfully | `null` |
| Partial but usable | Evidence exists, but unsupported types, retention, truncation, or incomplete successful coverage limits assessment | `null` unless an actual item failed |
| Missing | Required evidence was absent, failed, or could not be collected | Actual category when a request failed |
| Unsupported | The independently represented capability cannot be inspected | `null` |
| Failed | An attempted provider request errored, timed out, authenticated unsuccessfully, or returned an invalid response | Stable actual-failure category |
| Truncated | Evidence was collected and bounded; truncation is reported separately | `null` unless collection also failed |
| Not requested | The caller excluded the source | `null` |

Partial coverage is not automatically a provider failure. Unsupported source
types never produce `provider_upstream_error`. A successfully built partial
dependency index remains usable evidence and reports `completeness=partial`,
`assessment_complete=false`, `failed_items=0`, and `failure_category=null`.
Scripts, scenes, groups, templates, and dashboards remain bounded warnings and the
stable limitation `dependency_index_unsupported_source_types`.

`missing_evidence` means evidence was actually absent or failed. A partial source
with usable evidence instead appears under `coverage_limitations`. Actual item
failures may produce both usable supporting evidence and a bounded partial-item
limitation. Failure categories are reserved for actual attempted failures, while
partial coverage may still make `result_status=partial` and
`final_assessment=assessment_incomplete`.

## Event normalization

Evidence is normalized into deterministic events: `automation_triggered`,
`automation_condition_failed`, `automation_action_failed`,
`automation_completed`, `service_call_observed`, `state_changed`,
`entity_became_unavailable`, `entity_recovered`, `system_warning`,
`system_error`, `dependency_relationship`, `integrity_finding`,
`reliability_finding`, and `dynamic_reference_uncertainty`.

Timestamps are normalized to UTC RFC3339. Missing timestamps stay missing and
reduce confidence. Equal timestamps are ordered by source priority, type, and
stable ID. No clock-skew correction is inferred. Events deduplicate by their
source, source object, timestamp, type, entity, automation, run, and bounded
content fingerprint; distinct trace runs remain distinct.

## Windows, clusters, and rules

The configured window is the maximum cluster width unless direct structured
evidence links events. Relationship bands are immediate (0–30 seconds), near
(over 30 seconds through 2 minutes), and contextual (over 2 minutes through the
configured maximum). Timing alone never establishes cause.

Stable categories include:

- `trace_failure_with_unavailable_dependency`
- `trace_failure_with_missing_reference`
- `service_call_followed_by_state_change`
- `unexpected_state_change_with_automation_activity`
- `repeated_trace_failure_pattern`
- `integration_error_with_related_entities`
- `shared_dependency_failure`
- `configuration_integrity_contributor`
- `dynamic_reference_uncertainty`
- `recovery_after_dependency_restoration`
- `conflicting_evidence`
- `insufficient_evidence`

Hypotheses deduplicate by rule, incident cluster, automation, and primary entity
or dependency. Repeated supporting records are combined. Materially separated
clusters remain separate.

## Confidence, causality, and severity

Confidence is `confirmed`, `high`, `medium`, `low`, or `insufficient`.
`confirmed` requires direct structured linkage, such as a trace explicitly
identifying the failing target. Proximity alone cannot exceed medium. Free-form
text alone cannot produce confirmed or high confidence. Actually missing evidence
lowers confidence separately from partial usable coverage; contradiction is a
third distinct penalty. Dynamic expressions remain targetless.

Causal status is `confirmed_cause`, `probable_contributor`,
`possible_contributor`, `correlated_condition`, `contradictory_evidence`, or
`insufficient_evidence`. Severity measures operational impact, independently of
confidence: high is reserved for strong safety/security or repeated critical
failure evidence; medium for meaningful failures and unavailable dependencies;
low for isolated or weak anomalies; info for useful context. Alarming log wording
does not raise severity by itself.

Final assessment is one of `probable_cause_identified`,
`multiple_plausible_contributors`, `correlated_activity_found`,
`no_correlated_anomaly`, `insufficient_evidence`, or
`assessment_incomplete`. Result status is `success`, `partial`, or `failed`.

## Output, evidence, and token bounds

The response starts with incident identity, assessment, time window, focus,
whole-analysis counts, timeline summary, coverage, and provenance. Hypotheses
reference stable bounded evidence IDs. Supporting and contradicting references
are separate; missing evidence and coverage limitations remain visible.

Summary mode omits normalized event detail. Standard mode returns bounded
hypotheses and compact evidence references. Evidence mode adds only the bounded
events cited by the current hypothesis page. Full configurations, traces,
history payloads, registry payloads, logs, templates, tokens, authorization
headers, authenticated URLs, and secrets are never returned.

The implementation retains at most 1,000 normalized events and 1,000 evidence
references, inspects at most 200 System Log entries, uses at most 20 related
entities and 50 traces, and caps concurrent HA requests at five.

## Pagination and index behavior

Hypotheses use the corrected Beta 16–18 five-minute bounded sanitized snapshot.
The signed opaque cursor binds query fingerprint, analysis timestamp, incident
ID, evidence fingerprint, snapshot ID, offset, and—when requested—the final
committed dependency-index generation and fingerprint. `refresh_index` is
excluded from the continuation query fingerprint.

Continuation preserves incident ID, timestamp, totals, assessment, coverage,
evidence provenance, and index provenance. It performs zero HA calls, provider
dispatches, trace/history/logbook/log retrieval, index lookups/builds,
reclassification, or recorrelation. Tampering, expiration, mismatch, replacement,
and invalidation fail closed. The active in-memory index identity may be checked;
that is not an index lookup or build. Pagination snapshots are not a general
result cache and are removed after the final page.

If dependency and integrity context are both disabled, no dependency-index read
occurs and provenance says `requested=false`.

## Validation, audit, and observability

Field-level validation runs before provider or index access and before snapshot
creation. Errors use `invalid_request` details such as field, stable reason, and
operation. Cursor integrity uses `invalid_cursor`; unavailable, expired, or
replaced snapshot/index binding uses the established fail-closed cursor contract.

Audit records contain only supplied-focus booleans, related count, bounded
window/limit/context flags, cursor-presence Boolean, assessment, counts, coverage
state, result, endpoint categories, request ID, and version. They exclude raw
cursors, IDs lists, configurations, traces, history, logs, evidence text,
authentication material, and secrets.

`get_server_health.incident_correlation` reports request and terminal counts,
hypothesis/event aggregates, per-analysis unique sums, manual-review and source
failure events, truncation, cursor, index, and last-outcome counters. Request count
includes continuation. Terminal and whole-analysis aggregates count new analyses
once. Cursor failures are not failed new analyses. Result caching remains false.
`source_failures` counts actual failed sources or bounded failed source operations
only. Unsupported, not-requested, retention-limited, truncated, or otherwise
successful partial coverage does not increment source or provider failure counts.
The bounded audit summary records only coverage completeness, source-failure count,
coverage-limitation count, result, assessment, and whole-analysis counts.

## Security and limitations

Logs, traces, history, templates, and evidence summaries are untrusted inert
data. They cannot authorize or trigger another operation. Dynamic references do
not create invented entity IDs. Friendly names do not establish integration or
device relationships. Unsupported sources, retention gaps, missing timestamps,
external systems, and unobserved service context can conceal relevant evidence.
No remediation is applied and no executable remediation is generated.

## Example

```json
{
  "focus_entity_id": "input_boolean.away_mode",
  "automation_id": "1712345678901",
  "related_entity_ids": ["binary_sensor.front_door"],
  "lookback_hours": 24,
  "correlation_window_minutes": 10,
  "trace_limit": 10,
  "detail_level": "standard",
  "limit": 2,
  "refresh_index": true
}
```

Interpret each hypothesis with its evidence and coverage. A probable contributor
is not a command to change Home Assistant.

## Deployed Beta 19 read-only acceptance

1. Call `server_info`, `list_capabilities`, and `get_server_health`; verify
   `2.0.0-beta.19`, 37 registered/25 canonical tools, Engineering/read metadata,
   policy `bounded_incident_correlation_read`, no fallback, and planned
   `handoff_generation`.
2. Capture incident, dependency, provider, audit, governance, retry, and timeout
   baselines. Do not call a write tool.
3. With read-only discovery, select one automation with existing recent evidence
   and one related entity. Do not trigger anything.
4. Call `incident_correlation` with both focus values, a 24-hour or appropriate
   bounded lookback, 10-minute window, trace limit 10, all contexts, standard
   detail, low limit 1–2, and `refresh_index=true`.
5. Reconcile confidence, severity, causal, event, entity, and automation totals.
   Confirm coverage is explicit, contradiction is retained, dynamic evidence is
   targetless, and claims do not exceed evidence.
6. Follow at least two pages when present. Confirm stable incident ID, timestamp,
   totals, assessment, coverage, evidence and index provenance, with zero upstream
   calls, dispatch, index lookup/build, or recorrelation.
7. Cross-check selected references with applicable existing read-only tools:
   `get_entity`, `get_history`, `get_logbook`, `list_automation_traces`,
   `get_automation_trace`, `get_error_log`, `entity_dependency_analysis`,
   `configuration_integrity_analysis`, or `automation_reliability_analysis`.
8. Run a quiet target; require `no_correlated_anomaly`, `insufficient_evidence`,
   or `assessment_incomplete`, never a fabricated probable cause.
9. When naturally available, verify dynamic and conflicting cases. Otherwise use
   automated coverage and document that live evidence was unavailable.
10. Exercise missing focus, malformed ID, invalid bounds, too many related IDs,
    and cursor-plus-refresh; verify field details and zero upstream work.
11. Tamper with a cursor and change a continuation query; verify fail-closed errors,
    no upstream work, and cursor-only failure counters.
12. Recheck health and bounded audit. Confirm aggregates count each new analysis
    once, no prohibited fallback/write provider occurred, governance counts did
    not change, and neither beta nor production state was mutated.

Because Beta 19 adds a public tool, reconnect or recreate the beta connector if
the client retains a cached 36-tool catalog. Never expose the authenticated URL.

## Deployed Beta 20 read-only acceptance

1. Call `server_info`, `list_capabilities`, and `get_server_health`. Verify
   `2.0.0-beta.20`, 37 registered/25 canonical tools, unchanged Engineering/read
   metadata and `bounded_incident_correlation_read`, connected HA, no fallback,
   and planned `handoff_generation`.
2. Capture incident, provider/source failure, dependency-index, governance-plan,
   retry, timeout, and audit baselines. Do not call a write-capable tool.
3. When still present, use automation ID `1782920111688`, automation entity
   `automation.ha_critical_stale_data_monitor`, focus
   `climate.mudroom_thermostat`, and related entities
   `binary_sensor.garage_door_aqara_sensor` and
   `input_text.ha_stale_alert_signature`. Otherwise select an equivalent existing
   target through read-only discovery; do not manufacture activity.
4. Call `incident_correlation` with a 24-hour lookback, 10-minute window, trace
   limit 10, all contexts, standard detail, `limit=1`, and
   `refresh_index=true`.
5. Verify the dependency row is partial, assessment-incomplete, has nonzero
   examined evidence, zero failed items, and `failure_category=null`. Confirm
   warnings identify unsupported scripts, scenes, groups, templates, and
   dashboards, dependency relationships/events remain present, provider/source
   failure counters do not rise, and the overall assessment may remain partial.
6. Inspect hypotheses. `dependency_index` must not be in `missing_evidence` when
   usable dependency evidence was collected. The stable
   `dependency_index_unsupported_source_types` limitation must be present where
   relevant; supporting references, conservative confidence, targetless dynamic
   uncertainty, and contradiction must remain intact.
7. Follow at least two cursor pages. Confirm identical incident ID, timestamp,
   totals, coverage, failure categories, limitation identifiers, and index
   provenance with zero HA calls, provider dispatch, index work, collection,
   normalization, or recorrelation.
8. Run an entity-only request with dependency, integrity, and reliability context
   disabled. Verify dependency coverage is `not_requested`, provider is `none`,
   no index work occurs, and no dependency missing-evidence or limitation penalty
   appears.
9. Exercise missing focus/automation, cursor-plus-refresh, and a tampered cursor.
   Confirm field-level/fail-closed errors and zero upstream work.
10. Recheck health and bounded audit. Partial unsupported coverage increments
    `partial_count`, not source/provider failures; cursor pages do not duplicate
    aggregates; cursor failures remain separate; governance counts are unchanged.
11. Confirm audit output contains no full warnings, raw cursor, trace,
    configuration, logs, secrets, or authenticated URLs.
12. Confirm no service, trigger, entity/configuration write, governance operation,
    reload, restart, production access, or production modification occurred.

Beta 20 adds no tool or schema, so connector recreation is not normally required.
