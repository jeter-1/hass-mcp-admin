# Beta error taxonomy

The public `error_code` is the terminal client-facing category. Provider
`source_coverage.failure_category` describes where evidence acquisition failed; the two
fields are related but not interchangeable.

| Situation | Public error | Source-coverage failure | Upstream attempted | HA time |
| --- | --- | --- | --- | ---: |
| Invalid input rejected locally | `invalid_request` | `request_validation` | false | 0 ms |
| Home Assistant entity is absent | `entity_not_found` | `provider_upstream_error` | true | measured |
| Home Assistant rejects another request | `home_assistant_api_error` | `provider_upstream_error` | true | measured |
| Provider times out | provider/public timeout code | `provider_timeout` where applicable | true | measured |
| Policy rejects a route or fallback | `provider_prohibited` | `provider_prohibited` | false | 0 ms |

Raw validation exceptions and upstream bodies are never returned. Safe details are
limited to categories such as method, status, endpoint category, operation, resource
identifier, and exception type.

`get_server_health.data.metrics.recent_error_counts` counts terminal public tool
results, not internal exception propagation. One failed `tools/call` increments its
final public error code once. Provider request/failure counters record the selected
provider route separately. Retry attempts, if introduced, belong in retry telemetry
and do not masquerade as additional user-visible failures.

This contract is beta-only. Production v1.1.2 behavior is unchanged.

For reliability analysis, malformed internal automation IDs fail local validation
before any HA request. A syntactically valid missing internal ID remains
`automation_not_found`. Provider/source failures, retry attempts, and terminal public
errors retain separate counters. Invalid cursors and terminal failures increment the
reliability failure category once; cursor continuation pages do not repeat finding or
root-cause aggregates.

Beta 14 distinguishes a trustworthy empty trace result from evidence loss. A
successful zero-run list or a successfully parsed list with no in-window run may
return a successful evidence-gap finding. Malformed/filter/detail loss returns partial.
A failed or timed-out foundational trace list with no independent finding returns
`analysis_unavailable` or `provider_timeout`. Clock normalization failure maps to the
bounded `internal_server_error` before any upstream attempt. These terminal categories
increment once; trace-source/provider counters remain separate.
