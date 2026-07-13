# Single-automation reliability analysis

Beta 12 added the read-only `automation_reliability_analysis` tool. Beta 13 stabilized
correlation and grouping; Beta 14 unifies trace retrieval and request time. It accepts one
Home Assistant internal automation ID and returns bounded, evidence-backed findings;
it never triggers an automation, calls a service, creates a change plan, or changes
configuration.

## Input contract

- `automation_id` is required and must be the internal ID returned by
  `list_automations`, not an `automation.*` entity ID.
- `lookback_hours` defaults to 168 and is bounded to 1-720.
- `trace_limit` defaults to 10 and is bounded to 1-50.
- `detail_level` is `summary`, `standard`, or `evidence`.
- `limit` defaults to 20 and is bounded to 1-100 findings.
- `cursor` is an opaque, fingerprint-bound continuation token. It reads from a
  sanitized five-minute pagination snapshot and never repeats HA trace collection.
  Missing, expired, or mismatched snapshots return `stale_cursor`.

## Facilitator and provider architecture

The MCP handler calls `AutomationReliabilityAnalysisService`; it has no REST or
WebSocket client dependency. The service requests `reliability_analysis` from the
engineering orchestration provider. That provider composes explicitly approved,
read-only direct Home Assistant sources and attributes every source to
`direct_ha_api`. Standard HA MCP coverage is not claimed.

The `single_automation_reliability_read` policy permits only:

| Source | Exact purpose | Bound |
| --- | --- | --- |
| Automation config | One internal automation ID | One config |
| Automation state | Match the internal ID to current state | One matched automation |
| Blueprint source | Resolve actual behavior when `use_blueprint` is present | One local read-only source |
| Entity state | Check deduplicated exact references | At most 100 unique entities, concurrency at most 10 |
| Entity registry | Determine disabled/hidden status for selected references | One read, filtered before output |
| Traces | Detect observed stops and errors | 1-50 runs in the requested lookback |
| System Log | Correlate sanitized explicit identifiers | At most 20 matched entries |

There is no fallback, write permission, service execution, automation trigger,
restart, reload, delete, or physical action. Logbook/history is not fetched because no
initial deterministic rule requires it; coverage therefore reports `not_requested`.

## Deterministic rules

Each finding has a stable rule and finding ID, severity, confidence, status,
occurrence count, affected target, bounded evidence references, operational impact,
next investigation, and whether remediation would require governance.

| Rule ID | Evidence and interpretation |
| --- | --- |
| `automation_disabled` | Current state is off/disabled; informational because this may be intentional. |
| `missing_referenced_entity` | Exact structured reference returned not found. |
| `unavailable_referenced_entity` | Exact reference currently reports `unavailable`. |
| `unknown_referenced_entity` | Exact reference currently reports `unknown`. |
| `disabled_referenced_entity` | Exact reference is registry-disabled. |
| `repeated_trace_failure` | At least two traces fail at the same step with the same normalized error. |
| `repeated_condition_stop` | At least two traces stop at the same condition; explicitly not assumed defective. |
| `repeated_action_error` | At least two traces contain the same explicit action/service error. |
| `correlated_system_log_error` | Sanitized System Log evidence names the automation, or a dependency also named by a trace error. |
| `mode_concurrency_conflict` | Trace evidence reports max-exceeded/overlap rejection; mode configuration alone is insufficient. |
| `unresolved_dynamic_reference` | A dynamic template target cannot be resolved statically; coverage is incomplete. |
| `blueprint_evidence_unavailable` | The actual blueprint source cannot be read. |
| `no_recent_execution_evidence` | No trace exists in the lookback; this is an evidence gap, never an unreliable verdict. |
| `trace_evidence_unavailable` | Trace retrieval or timestamp parsing was incomplete; this is a source limitation, never proof that no run occurred. |

Beta 12 intentionally has no opaque reliability score, subjective complexity rule,
trigger-should-have-fired speculation, or threshold recommendation.

## Result and partial-evidence contract

The facilitator envelope includes the automation identity, timestamp, requested
lookback, assessment, counts by severity, bounded findings, configuration and evidence
fingerprints, source/trace/entity/System Log coverage, limitations, pagination,
timing, request ID, and success/partial state.

`no_findings` means only that no deterministic findings were detected within the
reported evidence. It is not a guarantee of health. `partial_evidence` is used when a
source is unavailable, fails, or is truncated; a dynamic reference is unresolved; a
blueprint is unavailable; or more finding pages remain. Confirmed findings remain
useful when another source fails independently.

Summary mode omits evidence details. Standard mode includes compact evidence
summaries. Evidence mode expands stable references but never returns full traces,
automation configuration, blueprint source, entity attribute dumps, or unrestricted
logs.

## Security and performance

All source content is untrusted data. System Log content passes through the Beta 11
recursive sanitizer before correlation. Returned automation identity text is also
sanitized. Evidence text cannot authorize a tool call or action. Audits contain bounded
request arguments, resource IDs, result status and request ID—not findings,
trace/configuration content, or evidence payloads.

Exact entity lookups are deduplicated and bounded, reads use bounded concurrency,
trace and log retrieval are capped, individual entity/trace failures do not abort
other evidence, and the total analysis has a configured timeout. Beta 12 does not
cache reliability results. Beta 13 reports `cache_supported: false`,
`cache_counters_active: false`, and per-response `cache.status: not_configured`
instead of presenting zero counters as an active cache.

## Beta 13 correlation and timestamp contract

Every public reliability timestamp is an RFC 3339 UTC string ending in `Z`. Trace
intervals use the stable `started_at` and `finished_at` keys. Missing timestamps remain
absent, malformed values are omitted, and neither case is replaced with the current
time. `first_observed` is the earliest parsed occurrence and `last_observed` is the
latest, regardless of source ordering.

System Log correlation is deterministic. Accepted bases are
`automation_entity_id_exact`, `automation_internal_id_exact`,
`failed_dependency_entity_id_exact`, and `trace_service_error_signature`. Canonical
identifiers use token boundaries. Friendly names, temporal proximity, generic task or
executor text, and substrings are insufficient. Evidence exposes only the bounded
basis enum and derived confidence, never the matched secret-bearing text.

`system_log/list` is a bounded, deduplicated, in-memory snapshot. Coverage therefore
distinguishes `snapshot_completeness` from `retention_coverage`; retention for the
requested lookback is reported `unknown`. This auxiliary limitation is always visible
but does not make independently supported trace/config findings fail or disappear.

## Root causes, timing, and pagination

Distinct rule findings remain available, but findings with the same automation,
failure step, sanitized error signature, affected dependency, and overlapping runs
share a stable `root_cause_group_id`. Standard/evidence responses include bounded
`root_cause_groups`; summary responses include only `unique_root_cause_count` and
separate finding/root-cause severity counts.

`home_assistant_ms` remains the shared cumulative duration of all HA attempts for
compatibility. The unambiguous companion fields are
`home_assistant_cumulative_attempt_ms`, `home_assistant_wall_clock_span_ms`,
`home_assistant_request_count`, and `provider_operations_concurrent`. Cumulative work
can exceed request wall time when reads overlap. Cursor pages count as calls but only
the first page updates cumulative finding, root-cause, source, trace, and entity
aggregates.

## Beta 14 analysis time and shared trace contract

The service captures exactly one injected, timezone-aware UTC instant at request start.
The normalized RFC 3339 `Z` value is reused for the inclusive lookback cutoff, result
metadata, provider request, evidence fingerprint, and every cursor page. An invalid or
naive clock value fails closed before Home Assistant access; a successful or partial
analysis can never return a null `analysis_timestamp`.

`list_automation_traces` and reliability collection call the same `trace/list` payload
and sanitizer-backed normalizer. Accepted starts include `Z`, explicit offsets,
fractional seconds, permitted numeric epochs, and Home Assistant `{start, finish}`
intervals. Valid starts are converted to UTC instants before comparison. A missing
finish is allowed; missing, malformed, or naive starts are counted as parsing loss and
never replaced with the current time. Runs exactly on the cutoff are included.

Normalized headers are deduplicated by run ID, ordered by instant and run ID, bounded
before detail retrieval, filtered before `trace/get`, and capped by `trace_limit`.
Coverage reports `collection_state`, `trustworthy_empty`, the normalized cutoff, and
bounded counts for upstream, considered, parsed, inside-lookback, selected, retrieved,
failed, malformed, missing-start, bad-start, bad-finish, and duplicate runs.

`no_recent_execution_evidence` is permitted only for a successful zero-run list or a
successful parsed list whose runs are all outside the lookback. Malformed headers,
list failure, timeout, detail loss, or unexplained filtering cannot produce that
finding. Malformed/detail/truncation loss is partial. A foundational trace-list failure
without an independent finding fails the analysis; independent findings may remain a
truthful partial result.

Reliability-result caching remains unsupported. A cursor may reuse only a bounded,
sanitized public-output pagination snapshot (maximum 16, five-minute TTL); it cannot
serve a new analysis and is removed after the final page. This prevents repeated HA
reads and aggregate counter inflation without concealing new evidence on a new call.

## Known limitations

- Static extraction cannot resolve every dynamic Jinja target.
- Trace shape varies across Home Assistant releases; only explicit errors, stops and
  concurrency signals are classified.
- No-event evidence is unavailable, so the analyzer cannot prove that a trigger
  should have fired.
- Logbook/history is intentionally unused by the initial deterministic rule set.
- Home Assistant retains only a bounded trace history, so a successful lookback query
  cannot prove that older executions never occurred.
- Beta 14 does not change the 34-tool manifest; connector recreation is not required.
