# v2 Beta Response, Error, Audit, and Observability Contracts

These contracts apply only to `hass_mcp_engineering_beta`. Production v1.1.2
runtime behavior and logging remain unchanged.

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
  "details": {"method": "GET", "endpoint_category": "error_log"},
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

Redaction is recursive and deterministic. Dictionary keys matching
`access_secret`, `authorization`, `cookie`, `set-cookie`, `token`, `password`,
`api_key`, or `credential` are replaced with `<redacted>`. Any occurrence of
the configured access secret inside another string becomes `<access_secret>`.
Long strings are bounded before logging or auditing.

Never log or audit:

- authorization header values;
- cookies or session identifiers;
- raw tokens, passwords, API keys, or credentials;
- the complete access secret;
- complete authenticated MCP paths; or
- unbounded request/response payloads.

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

- total request and tool-call counts;
- total request duration samples;
- tool execution duration in migrated responses;
- Home Assistant API duration samples;
- retry count (currently zero because automatic retries are not enabled);
- timeout count and per-request timeout occurrence;
- HTTP response status;
- safe Home Assistant endpoint categories; and
- recent error counts by stable code.

Metrics are process-local and reset on restart. They are intentionally not a
durable analytics or tracing backend.

## `get_server_health`

`get_server_health(check_ha=true)` is beta-only and returns a structured success
envelope containing safe operational data:

- beta identity and version;
- runtime mode and uptime;
- optional Home Assistant connection state;
- request and Home Assistant latency summaries;
- registered tool count;
- audit and logging subsystem state;
- recent safe error counts;
- rate-limiter summary;
- redaction and configuration-validation state;
- tool-call, retry, and timeout counts.

It never returns the secret, tokens, headers, cookies, complete MCP endpoint
paths, private request payloads, or raw audit/log records.

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

The canonical 25-tool classification remains Native 8, Transitional 10,
Delegated 4, Deprecated 3, with 6 planned capabilities. `get_server_health` is
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
