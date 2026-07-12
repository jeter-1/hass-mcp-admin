# Single-automation reliability analysis

Beta 12 adds the read-only `automation_reliability_analysis` tool. It accepts one
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
- `cursor` is an opaque, evidence-fingerprint-bound pagination cursor. A changed
  configuration or evidence set invalidates an older cursor with `stale_cursor`.

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
cache reliability results; health therefore reports zero cache hits/misses.

## Known limitations

- Static extraction cannot resolve every dynamic Jinja target.
- Trace shape varies across Home Assistant releases; only explicit errors, stops and
  concurrency signals are classified.
- No-event evidence is unavailable, so the analyzer cannot prove that a trigger
  should have fired.
- Logbook/history is intentionally unused by the initial deterministic rule set.
- A new MCP tool changes the manifest to 34 tools; refresh or recreate ChatGPT/Claude
  beta connectors after deployment if they retain a cached 33-tool manifest.
