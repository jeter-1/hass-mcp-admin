# v2 Beta Response, Error, Audit, and Observability Contracts

These contracts apply only to `hass_mcp_engineering_beta`. Production v1.1.2
runtime behavior and logging remain unchanged.

Governed automation changes add safe lifecycle events and bounded governance
health counts. Proposed/current configurations, approval notes, hashes, secrets,
webhook IDs, authorization material, and authenticated paths are excluded from
generic audit parameters. See
[`../docs/CHANGE_GOVERNANCE.md`](../docs/CHANGE_GOVERNANCE.md).

## Structured responses

Migrated beta tools return JSON strings containing one of two envelopes. Tool
argument schemas are unchanged; remaining compatibility tools keep their legacy
responses until they are migrated individually.

Successful response:

```json
{
  "success": true,
  "operation": "server_info",
  "summary": "Returned beta server identity and runtime metadata.",
  "data": {},
  "warnings": [],
  "metadata": {},
  "timing": {
    "total_ms": 4.2,
    "tool_ms": 3.7,
    "home_assistant_ms": 2.1,
    "retry_count": 0,
    "timeout_occurred": false
  },
  "request_id": "caller-or-generated-request-id"
}
```

Failure response:

```json
{
  "success": false,
  "operation": "get_error_log",
  "error": "HomeAssistantTimeoutError",
  "error_code": "home_assistant_timeout",
  "message": "Home Assistant timed out.",
  "details": {"method": "WEBSOCKET", "endpoint_category": "system_log/list"},
  "retryable": true,
  "warnings": [],
  "metadata": {},
  "timing": {
    "total_ms": 60000.0,
    "tool_ms": 60000.0,
    "home_assistant_ms": 60000.0,
    "retry_count": 0,
    "timeout_occurred": true
  },
  "request_id": "caller-or-generated-request-id"
}
```

Response output is bounded by `response_size_limit`. Diagnostic details contain
safe categories and identifiers, not raw response bodies, credentials, headers,
cookies, or authenticated endpoint paths.

## Error-code catalog

Codes are stable machine-readable values. HTTP mappings describe gateway or
transport equivalents; MCP mappings describe the closest JSON-RPC category.

| Code | Retryable | HTTP | MCP mapping |
| --- | --- | ---: | --- |
| `authentication_failure` | no | 404 | invalid request |
| `authorization_failure` | no | 403 | invalid request |
| `invalid_request` | no | 400 | invalid request |
| `validation_failure` | no | 422 | invalid params |
| `home_assistant_unavailable` | yes | 503 | internal error |
| `home_assistant_api_error` | no | 502 | internal error |
| `home_assistant_timeout` | yes | 504 | internal error |
| `entity_not_found` | no | 404 | invalid params |
| `automation_not_found` | no | 404 | invalid params |
| `unsupported_operation` | no | 405 | method not found |
| `configuration_conflict` | no | 409 | invalid request |
| `rate_limit_exceeded` | yes | 429 | server error |
| `internal_server_error` | no | 500 | internal error |

Unexpected exception messages are not returned. The safe response contains the
exception type and catalog message; internal details remain in correlated,
redacted application logs.

Taxonomy definitions allow only safe diagnostic field categories:
`exception_type`, `operation`, `resource_id`, `status`, and
`endpoint_category`. Individual mappings may return a subset.

Provider source coverage uses a separate stable failure taxonomy. A request rejected
locally before any provider I/O uses `request_validation`, sets
`upstream_attempted=false`, and reports `home_assistant_ms=0`. It must not be labeled
`provider_upstream_error`. Upstream rejections retain `provider_upstream_error`.

## Request correlation

Every HTTP MCP request receives a request ID. A caller may provide
`X-Request-ID` containing 8-128 letters, digits, dots, underscores, colons, or
hyphens. Invalid or absent values are replaced with a random UUID-derived ID.

The ID is available through request context to:

- migrated tool response envelopes;
- structured application and error logs;
- audit records;
- HA timing and endpoint-category telemetry; and
- the `X-Request-ID` response header.

Request IDs are correlation labels only. They never contain or derive from the
access secret, token, cookie, request body, or client IP.

## Audit-record schema

Tool-call records are JSON Lines with these fields:

| Field | Meaning |
| --- | --- |
| `timestamp` | UTC ISO-8601 record time |
| `request_id` | safe correlation ID |
| `server_version` | beta server version |
| `tool_name` | public MCP tool name |
| `capability_classification` | canonical status or additive `beta_native` |
| `operation_category` | capability category |
| `access` | `read` or `write`, derived from risk metadata |
| `authenticated` | gateway authentication result |
| `caller_id` | one-way, truncated hash of caller address when available |
| `parameters` | bounded, recursively redacted arguments |
| `result_status` | success, failure, rejected, or unknown |
| `error_code` | stable code when applicable |
| `duration_ms` | total gateway request time |
| `ha_endpoint_categories` | safe categories such as `config/core` |
| `resource_ids` | bounded arguments whose names end in `_id` |

Audit output is configured with `audit_enabled`, `audit_path`, and
`audit_max_payload_chars`. Oversized records become a small truncation record.
Write failure increments safe health counters and emits a structured application
error; it does not raise into a read-only tool call.

## Redaction rules

Redaction is centralized, recursive, deterministic, idempotent, and fail-closed.
Key-aware rules cover verified authentication, webhook, Matter commissioning, auth
flow, cookie, and password fields without treating every field named `code` as secret.
Every string leaf is then scanned for credential-bearing URLs, query/fragment
parameters, login-flow and webhook paths, bearer/JWT-like credentials, Matter `MT:`
payloads, and quoted Python/JSON assignments. Stable category markers are emitted.
Redaction always precedes string and payload bounding.

Never log or audit:

- authorization header values;
- cookies or session identifiers;
- raw tokens, passwords, API keys, or credentials;
- the complete access secret;
- complete authenticated MCP paths; or
- unbounded request/response payloads.

If sanitation of one field raises, that field becomes
`[REDACTED:sanitization_failure]`; the raw value is never returned or logged. Existing
markers are not reprocessed. System Log redaction telemetry contains only the applied
flag, field count, bounded unique categories, and fail-closed state.

`redaction_enabled` must remain true; startup validation rejects false.

## Application logging conventions

Beta logs are JSON objects with consistent fields:

- `level`: debug, info, warning, or error;
- `request_id`: current correlation ID;
- `subsystem`: application, gateway, audit, compatibility, or another module;
- `event`: stable event name;
- `message`: human-readable safe summary;
- `context`: recursively redacted safe fields; and
- `exception_type`: present for captured exceptions without raw exception text.

Use DEBUG for bounded diagnostic state, INFO for lifecycle and successful
requests, WARNING for recoverable degradation, and ERROR for failed operations
or output subsystems. Do not add ad hoc `print` statements to beta runtime code.

## Observability fields

The in-memory metrics registry captures:

- completed transport-request, MCP-operation, and tool-call counts;
- completed JSON-RPC operation latency for methods such as `initialize`,
  `tools/list`, and `tools/call`;
- tool-processing latency independently from the enclosing transport request;
- Home Assistant API duration samples;
- retry count (currently zero because automatic retries are not enabled);
- timeout count and per-request timeout occurrence;
- HTTP response status;
- safe Home Assistant endpoint categories; and
- recent error counts by stable code.

`recent_error_counts` counts terminal public tool outcomes: one failed `tools/call`
increments its final public error code once. REST/WebSocket detection, facilitator
conversion, and response wrapping do not add duplicate counts for the same call.
Provider failures remain separately counted by provider, and retry attempts remain in
retry telemetry.

Phase 3A also tracks bounded provider-routing counters: requests, successes, and
failures by safe provider identity; partial results; fallback attempts and successes;
prohibited fallback attempts; and evidence truncation. Counters contain no queries,
evidence payloads, credentials, or provider URLs.

Dependency analysis exposes request/success/partial/failure counters, index builds
and failures, cache hits/misses, invalidations, bounded source/edge counts, index age,
last successful build, cumulative truncation events, and the current index's unresolved
dynamic-reference count. Entity IDs
are never metric labels. Health exposes no findings or raw configuration.

Long-lived GET/SSE stream and idle session lifetime is excluded from operational
latency. Transport completions are counted separately without treating session
lifetime as request-processing time. Metrics are process-local and reset
deterministically on restart. They are intentionally not a durable analytics or
tracing backend.

## `get_server_health`

`get_server_health(check_ha=true)` is beta-only and returns a structured success
envelope containing safe operational data:

- beta identity and version;
- runtime mode and uptime;
- optional Home Assistant connection state;
- MCP operation, tool-processing, and Home Assistant latency summaries;
- completed transport-request count with an explicit indication that session
  lifetime is excluded;
- registered tool count;
- audit and logging subsystem state;
- recent safe error counts;
- rate-limiter summary;
- redaction and configuration-validation state;
- tool-call, retry, and timeout counts.
- safe provider-routing counters and explicit `standard_ha_mcp_delegation=unavailable`.

It never returns the secret, tokens, headers, cookies, complete MCP endpoint
paths, private request payloads, or raw audit/log records.

The delegation diagnostic reflects current reality: Beta 12 verifies the Home Assistant
MCP endpoint but does not configure or call it because Assist lacks exact mappings for
the approved administrative reads. It must not be interpreted as a Home Assistant API
connectivity failure. Provider failures and partial coverage remain visible; the four
administrative reads select explicit direct policies without fallback, while direct
write expansion remains prohibited.

Every provider-routed canonical call contributes to these counters. `get_entity`,
`list_areas`, `search_services`, and `list_services` are attributed to `direct_ha_api`;
they never claim `standard_ha_mcp`. Other transitional and direct-required exceptions
are likewise attributed to the provider actually used.
Lifecycle labels do not substitute for runtime provider attribution.

Dependency timing separates current request duration, cache lookup duration, original
index-build duration, and cached source-provenance duration. Cached provenance is not
reported as work repeated during the current request.

## Structured Home Assistant error entries

`get_error_log(tail_lines=1..200)` uses Home Assistant's admin-only
[`system_log/list` WebSocket command](https://github.com/home-assistant/core/blob/2026.7.2/homeassistant/components/system_log/__init__.py).
[Home Assistant 2026.7.2 registers the historical `/api/error_log` REST view only
when file logging is configured](https://github.com/home-assistant/core/blob/2026.7.2/homeassistant/components/api/__init__.py);
[HA OS normally sends Core logs to the system journal](https://www.home-assistant.io/common-tasks/os/),
so that conditional view can return 404. Beta 11 does not request broad Supervisor log
permissions.

The compatible input name remains `tail_lines`, but the result now contains
newest-first, deduplicated structured warning/error entries rather than raw file lines.
The envelope identifies `home_assistant_system_log`, returned/available counts,
effective and maximum limits, explicit truncation reasons, and
`content_is_untrusted_data=true`. Each string and the total payload are bounded. Empty
System Log state is a successful empty list; 404, permission, timeout, unavailable, and
malformed responses remain explicit failures.

Beta 11 sanitizes the complete recursive `system_log/list` result before applying
`tail_lines`, limiting message history, renaming known fields, preserving safe unknown
fields, calculating payload size, or serializing the facilitator envelope. Both
`message` and `exception`, including nested serialized structures and future fields,
pass through the same pipeline. The response reports `redaction_applied`,
`redacted_field_count`, `redaction_categories`, `sanitization_failed_closed`, and only
safe sanitation warnings. Log text remains evidence rather than instructions; it
cannot authorize or trigger another operation.

## Automation reliability analysis telemetry

Beta 12 adds a bounded `automation_reliability_analysis` health group:

- request, success, partial, and failure counts;
- cumulative finding counts by severity;
- traces and referenced entities examined;
- source failures and findings-truncated events;
- last successful analysis timestamp and last bounded failure category; and
- explicit zero cache hits/misses because Beta 12 does not cache reliability results.

One terminal analysis updates success, partial, or failure exactly once. Provider
routing separately records the engineering orchestration request and every attempted
direct evidence source. Health never contains an automation ID, configuration,
friendly name, trace, System Log message, finding, evidence reference, or authenticated
URL. Source-specific duration is returned only in the correlated tool response.

## Startup configuration validation

Startup validates:

- a Home Assistant API token and HTTP(S) URL;
- an access secret of at least 24 characters;
- port range 1-65535;
- non-empty audit target when auditing is enabled;
- audit payload minimum size;
- log level (`DEBUG`, `INFO`, `WARNING`, or `ERROR`);
- HA timeout greater than zero and at most 300 seconds;
- response-size limit between 1,024 and 1,000,000 characters; and
- mandatory redaction.

Failures emit a structured, redacted error and terminate without echoing unsafe
configuration values.

## Compatibility and remaining migrations

The Beta 12 canonical 25-tool classification is Native 8, Transitional 14, and
Deprecated 3 after Phase 3C capability-truth alignment, with 3 remaining planned
capabilities. `get_server_health` is
advertised separately as additive `beta_native` metadata so these counts do not
change.

This change migrates `server_info`, `list_capabilities`, `get_error_log`, and
`get_entity`, plus the new health tool and representative HA 4xx/5xx failure
paths. Remaining tools retain their published names, argument schemas, and
legacy response behavior. `get_server_health` is registered explicitly on the
FastMCP instance returned by the beta registry and is verified through real
`tools/list` and `tools/call` integration tests.

Upstream Home Assistant failures set the request telemetry error code before
control returns through FastMCP. The client response, structured request logs,
and final audit record therefore share the same request ID and stable error
code; HTTP 404 entity lookups map to `entity_not_found`, while other upstream
rejections map to `home_assistant_api_error`.

For each future migration:

1. keep the public name and generated argument schema unchanged;
2. add behavior and failure regression tests first;
3. use `run_structured` and the shared response/error contracts;
4. route HA calls through observable clients;
5. add only safe audit resource identifiers and endpoint categories;
6. preserve destructive-service confirmation behavior; and
7. migrate one coherent read-only family before any write/governance work.
