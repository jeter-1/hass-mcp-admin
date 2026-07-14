# Beta error taxonomy

Beta 23 separates terminal tool errors from provider outcomes. An
`invalid_request`, `invalid_cursor`, authentication failure, or pre-dispatch rate
limit rejection may increment its tool/transport error category, but it does not
increment provider requests or failures. Provider failure categories require an
attempted, attributable provider operation. A selected provider in routing
metadata is not an attempted provider.

Beta 22 validation returns `invalid_request` with bounded field/reason/operation
details before HA, provider, governance-storage, index or snapshot work. Signed
cursor tampering/mismatch uses `invalid_cursor`; unavailable/expired snapshots or
replaced index state use `stale_cursor`. Partial or unsupported coverage is not a
failure category.

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

Beta 16 change-impact validation uses a stable field-level contract:

```json
{
  "field": "replacement_entity_id",
  "reason": "required_for_rename",
  "operation": "rename_entity"
}
```

Stable reasons cover a missing, malformed, same-as-source, or operation-forbidden
replacement; malformed target IDs; unsupported operations or sources; bounded
numeric/detail fields; and cursor use with first-page-only refresh. These details
contain no raw exception, stack trace, filesystem path, upstream body, cursor,
secret, or authenticated URL. Validation performs no Home Assistant request,
dependency-index lookup/build, or pagination-snapshot creation.

`get_server_health.data.metrics.recent_error_counts` counts terminal public tool
results, not internal exception propagation. One failed `tools/call` increments its
final public error code once. Provider request/failure counters record actual
dispatched provider operations separately. Retry attempts, if introduced, belong in retry telemetry
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

For change-impact pagination, `invalid_cursor` means the opaque value failed
format, field, offset, or signature validation. `stale_cursor` means the signed
value is intact but its snapshot expired/disappeared, its result-shaping query no
longer matches, or its committed dependency-index generation was replaced or
invalidated. Cursor failures have dedicated health counters and are not terminal
failures of new analyses. `refresh_index=true` is valid only without a cursor.

`configuration_integrity_analysis` uses the same cursor categories. Its
first-page validation returns `invalid_request` with stable `field`, `reason`,
and `operation` details for invalid detail level, 1–100 limit, source type,
finding type, Boolean orphan flag, or incompatible continuation options.
Validation occurs before provider dispatch, HA access, dependency-index access,
or snapshot creation. An intact cursor bound to changed query semantics, an
expired snapshot, or a replaced index returns `stale_cursor`; malformed or
tampered cursor material returns `invalid_cursor`.
