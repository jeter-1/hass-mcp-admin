# v2 Beta Response, Error, Audit, and Observability Contracts

## Dev15 contract-level compatibility health

Dev15 separates upstream version evidence, per-tool compatibility, dashboard
admission, and operational reachability. The active decision is
[`ADR-006`](../docs/architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md);
the release-specific sections below remain historical evidence for the fields
they introduced.

`/health` remains process liveness. The separate bounded `/ready` response
contains only `ready`, `initial_reconciliation_required`,
`initial_reconciliation_complete`, and
`status=ready|initial_reconciliation_pending`. When upstream is configured,
`/ready` and authenticated MCP traffic return HTTP 503 until
`reconcile_until_initialized` returns its first stable or terminal result. An
unconfigured gateway reports ready immediately for its truthful static catalog.

`get_server_health.upstream_read_gateway` reports:

- `version_status=reviewed_exact|rejected_unreviewed|rejected_identity|rejected_protocol|not_observed`
  beside the bounded reviewed and observed versions;
- `observed_upstream_server_name`, `observed_upstream_server_version`,
  `observed_protocol_version`, and
  `observed_identity_status=accepted|rejected|not_observed`;
- `compatibility_status=exact|partial|incompatible|reconciling|unavailable`;
- reviewed automatic-read, exact-matched, dynamically exposed, missing,
  quarantined, and unreviewed observed counts;
- `schema_mismatch_count`, `description_semantics_mismatch_count`,
  `annotation_mismatch_count`, `output_contract_mismatch_count`, and
  `runtime_contract_mismatch_count`;
- `description_semantics_mismatch_count` specifically counts exact bounded
  full-runtime-description fingerprint mismatches; raw descriptions are never
  emitted;
- bounded quarantine entries containing only tool identity, a stable reason,
  and expected/observed contract fingerprints;
- fast transport-startup `retry_count`, `next_retry_delay_seconds`, and
  `reconciliation_status` separately from
  `compatibility_reprobe_interval_seconds`,
  `last_compatibility_reprobe_at`, `next_compatibility_reprobe_at`, and
  `compatibility_reprobe_status`; and
- `writes_allowed=false`, `direct_ha_fallback_allowed=false`, and zero fallback
  authority.

The reviewed automatic-read accounting remains internally complete:

```text
exact matched + missing + quarantined = reviewed automatic reads
```

`exact` means all 26 reviewed reads match under the compiled exact 7.14.1
release/profile. `partial` means at least one remains usable while another is
missing or quarantined. `incompatible` means an authorized stable catalog was
evaluated but no reviewed read is safe. `reconciling` means a bounded catalog
reconciliation is in progress. `unavailable` means the endpoint is
unconfigured, identity/protocol/release authority failed, or a catalog cannot
currently be obtained. Additional unreviewed or blocked tools are reported
separately and do not downgrade `exact` while all reviewed reads still match.

Every delegated call performs a same-session `tools/list` check immediately
before `tools/call`. Missing, duplicate, changed selected-target, or
unreviewed-version evidence stops before dispatch. A matching self-advertised
target cannot authorize another release. A different server name, malformed
version, or unsupported protocol likewise remains a global pre-dispatch
failure.

Each call acquires an immutable current-generation route snapshot under a short
lease, then performs network I/O without holding a global registry lock. A
route retired before pre-dispatch validation cannot dispatch. A call already
committed after successful validation may finish, but cannot republish or
revive a retired generation. Unrelated reads and reconciliation therefore
remain independently schedulable.

Unrelated new, changed, malformed, or duplicate unreviewed descriptors do not
block that target-local check. Reconciliation records them only as bounded,
redacted anomalies, and they never become registered or callable.
The whole-catalog fingerprint is best-effort diagnostic evidence; if
unreviewed data is noncanonical, the field is unknown and independently
verified per-tool decisions remain authoritative.

Generic delegated-call audit records retain only the bounded same-session
upstream version evidence and accepted/rejected identity status alongside the
reviewed route and argument field names. Raw catalogs, descriptions, schemas,
endpoints, and credentials remain excluded.

With 41 static tools, complete generic admission reports 26 delegated and 67
registered tools. One missing or quarantined read reports 25 delegated and 66
registered. The two dashboard wrappers are static tools; their registration
does not claim that `upstream_dashboard` is currently compatible or reachable.

`get_server_health.upstream_dashboard` remains independent. Admission requires
an exact-version built-in or verified signed attestation before the compiled
family is evaluated. Missing exact authority, mismatch, or revocation is
reported as rejection and cannot fall back to an older contract or a
self-advertised compatible variant. An expired exact entry remains deny-only
and unavailable registry evidence cannot revive an older contract. Generic
quarantine does not change dashboard compatibility, and dashboard
incompatibility does not change generic-read counts.

The fast retry lane exposes bounded transport attempts, next delay, and status
for boot-order recovery. Connection and timeout failures use the capped fast
cadence; endpoint/session-not-ready evidence receives at most the documented
600-second host-reboot startup grace before falling to the slow lane. The slow
lane exposes a fixed interval plus last/next
timestamps and status. Waiting or probing for stable compatibility does not
remove an already admitted exact subset. Health includes no raw schema,
annotation document, remote description, endpoint, registry body, signature,
credential, or raw exception.

`stale_reprobe_retry_armed` reports the liveness bound for overlapping catalog
observations. A newer admission-relevant token equal to the discovery token
allows the stable generation to publish despite busy exact calls. A mismatch
discards the stale generation; only the first consecutive mismatch arms an
immediate retry, and later churn waits for the slow cadence. The token is
internal, excludes unreviewed content, and is never exposed as authority.

## RC2dev10 selected-attestation legacy fields

RC2dev9 correctly admitted live ha-mcp 7.14.1 through built-in entry
`ha-mcp-v7.14.1-68f386d9`, but four retained diagnostic comparisons still used
static 7.13.0 expectations. That produced false raw-schema,
reviewed-security, and published-runtime mismatches despite all authoritative
normalized matches being true. RC2dev10 takes each version-specific expected
legacy fingerprint from the exact selected attestation.

The legacy mapping is: `input_schema_match` compares the exact observed raw
`inputSchema`; `reviewed_security_contract_match` compares the retained
security descriptor projection; `runtime_descriptor_match` compares the
reviewed fixture descriptor; and `published_runtime_descriptor_match` compares
the exact published descriptor. The authoritative admission mapping remains
`input_contract_match`, `security_contract_match`, `output_contract_match`, and
`runtime_contract_match`, computed from normalized semantic contracts. These
families are intentionally not interchangeable.

The active profile is `ha_mcp_dashboard_read_v2`. Exact releases are selected
through reviewed attestations. No generic version range, new tool, new route,
write, screenshot, preference operation, or fallback is introduced.

## RC2dev9 upstream admission health

`get_server_health.upstream_dashboard` adds bounded contract-family admission
evidence: `admission_status`, `admission_source`, `contract_family`, attestation
ID, observed/attested versions, attested source/image identity, normalized input,
security, output and runtime fingerprints/match flags, and revocation status.
Registry fields report enabled state, sequence/generated/age, refresh status,
last successful refresh, bounded failure category, signature state, cache
state/age and fixed refresh/hard-age limits.

Accepted statuses are `admitted_builtin_attestation` and
`admitted_signed_registry_attestation`; rejections distinguish unknown release,
contract mismatch, registry unavailable, signature failure, expired attestation
and revoked attestation. The whole-catalog fingerprint remains diagnostic only.
No field contains the endpoint, registry body, signature, public-key value,
filesystem path, raw schema/description or raw exception. Enabled upstream
capabilities remain only dashboard inventory and exact configuration evidence;
writes, screenshots and preferences remain false.

## RC3A dashboard-provider observability

`get_server_health.upstream_dashboard` reports configured/credential state,
reachability, capability status, sanitized live upstream identity, MCP protocol,
tool count, required-tool/schema compatibility, expected and observed input-schema,
reviewed-security-contract, runtime-descriptor, and catalog fingerprints,
last success timestamps, connection and tool-call latency, request/success/
timeout/reconnect counts, categorized failures, session state, and
`writes_allowed=false`.

Historical RC3A trust reporting distinguished `contract_read_only` from
`reviewed_argument_constrained`. Its deprecated single-release profile ID was
`ha_mcp_7_13_dashboard_read_v1`; RC2dev9 superseded it with active family
`ha_mcp_dashboard_read_v2` and exact-release attestations. The retained RC3A
section describes the original 7.13.0 name/version checks, contract-match state, active argument
constraints, and explicit `screenshots_allowed=false`,
`preference_writes_allowed=false`, and `writes_allowed=false`. It never claims
that the mixed upstream tool is globally read-only.

It never reports the endpoint, host, port, secret path, query, credentials,
headers, raw schemas, or raw exception text. Stable transport categories
distinguish `authentication_failed`, neutral `endpoint_rejected`,
`connection_failed`, and genuine `timeout`, followed by protocol, response,
capability, identity, version, input-schema, security-contract, annotation,
output-contract,
prohibited-argument, hash-contract, upstream, response-size, and internal
failures. Dashboard calls add
separate upstream duration/count fields to response timing without changing
direct Home Assistant timing semantics. Audit records contain only the list
limit or exact canonical dashboard path, force-reload flag, and provider; they
exclude endpoint and dashboard content.

The normal contract-level mode requires `readOnlyHint=true`. The current
argument-constrained mode requires exact `ha-mcp` identity, an exact reviewed
release attestation, the compiled family protocol and normalized contracts,
exact reviewed safety annotations, and exact non-screenshot invocation shapes.
Full runtime-descriptor drift is separately reported.
`descriptive_metadata_only` drift is non-blocking only while the security
projection remains exact; semantic drift makes the capability unavailable.
Health does not return the fixture, full schema, raw descriptor, or raw
description.

The reviewed fixture raw descriptor is `170c2aac...`; the reproduced published
7.13.0 runtime is `dd12cba0...` because upstream middleware adds only bounded
conversation-agent exposure/pinning metadata. Health reports both the fixture
and published expected hashes, the observed hash, exact match Booleans, and the
bounded drift classification. This warning does not count as a provider
failure.

`standard_ha_mcp_delegation` remains `unavailable`. The two new beta-native
tools raise the registered count to 40; the canonical count remains 25.

## RC2 build provenance and compatibility

The existing `server_info` fields `build_sha` and `build_time` report validated
image-build inputs in deployed RC images. They do not appear in health,
capability, audit-redaction, provider, or governance counters. Missing or
invalid local inputs report `unknown`. RC2 otherwise preserves every Beta 26
observability field and counter meaning, including idempotent expiry events.
The explicit `search_entities` direct route increments direct request/success or
failure exactly once, treats truncation as a partial result rather than a
provider failure, and never increments Standard HA MCP or fallback counters.

## Beta 26 expiry lifecycle observability

`change_plan_expired` and `external_approval_expired` are lifecycle transition
events, not read counters. Each transition is persisted and audited once.
Repeated `get_change_plan`, `list_change_plans`, `get_server_health`, Ingress,
or handoff reads of the same effective state do not update the plan or duplicate
events, audit entries, or structured logs.

`pending_challenge_count` is computed from the resolved current lifecycle. It
excludes challenges whose expiry has passed even when the first observation is
a health call. The same resolver supplies public plan reads, the Ingress inbox,
approval requests, apply, rollback, and handoff governance context. These
metrics remain observational and cannot grant authority. No provider failure is
recorded for a local expired-approval refusal because no provider dispatch
occurred.

## Beta 25 external-approval observability

Governance health additively reports `external_approval_enabled`,
`ingress_approval_ui_configured`, authority version 2, bounded pending/granted/
rejected/expired/invalidated/consumed counts and a safe last-failure category.
These fields never grant authority. A request through `approve_change_plan`
records `external_approval_requested`; only the private Ingress application can
record grant or rejection. Preapproval apply/rollback refusals occur before
provider write dispatch and therefore do not increment provider failures.

Audit excludes CSRF, cookies, raw headers, Ingress credentials, request notes,
full configuration/diffs and secrets. See
[`../docs/EXTERNAL_APPROVAL.md`](../docs/EXTERNAL_APPROVAL.md).

## Beta 24 routing and ingress semantics

A provider selected by policy but known unavailable before dispatch returns an
explicit unavailable result with `upstream_attempted=false`. It does not
increment provider request, success, or failure counters. Actual dispatch still
increments one request and then one success/partial-success or failure outcome.
Tool-level recent-error telemetry retains the terminal public unavailable error.

The rate-limiter health section reports the independently bounded client and
authentication-failure store sizes, the 1,000-entry bound, forwarded-header
trust state, and trusted-network count. It does not expose addresses. Store
pressure performs atomic LRU eviction and never resets the whole store.

RC2dev6 preserves those aggregate health fields and adds no public health-schema
surface. Audit queries distinguish `auth_failure` for ordinary 404 rejection,
`auth_failure_throttled` for authentication-bucket 429 rejection, and
`rate_limited` for authenticated general limiting. The event classes remain
exactly filterable and do not affect provider-operational, analysis, governance,
or fallback counters.

RC2dev7 makes the read-side classification semantic. Each nonempty bounded
JSONL line is parsed independently, and only
`record.get("event") == requested_event` matches. The routed audit reader
remains visible as `tool_call`; its nested filter argument, message text, or
exception data cannot match another class. Malformed, non-object, blank, and
oversized historical lines are skipped while later valid records remain
readable. The public response remains bounded JSONL with no new envelope or
health schema.

`get_audit_log` clamps reads to 1–500 lines. Refused legacy automation writes are
audited as bounded write intent with no payload, HA endpoint, provider request,
or provider failure.

## Beta 23 provider-routing counters

Provider selection and provider dispatch are distinct. The shared metrics API
requires an explicit dispatch assertion before it changes
`requests_by_provider`, `successful_requests_by_provider`, or
`failures_by_provider`. Request/cursor validation, authentication, rate limiting,
policy rejection, pre-provider application errors, and local signed-snapshot
continuations do not affect provider counters. They remain visible through tool,
cursor, transport, recent-error, and audit telemetry as applicable.

One dispatched complete operation increments request and success once. A usable
partial provider result also increments request and success once and increments
`partial_results`; that field describes provider operations returning partial
evidence, not every whole-tool `result_status=partial`. One attributable failed,
timed-out, or invalid-response operation increments request and failure once.
Source failures are evidence-source outcomes and remain separate: unsupported,
not-requested, retention-limited, or successful partial coverage is not a source
or provider failure. All provider-routing fields are cumulative process-level
metrics and reset deterministically on process restart or explicit metric reset.

## Beta 22 handoff counters

`get_server_health.handoff_generation` counts all requests, while terminal
outcomes and item aggregates count new handoffs only. Cursor failures and pages
are separate. `source_failures` counts actual failed operations;
`coverage_limitation_events` counts successful incomplete coverage. Open/risk/
recommendation/authorization/manual-review, truncation and index hit/miss counts
remain bounded and identity-free.

Coverage is normalized before counters are recorded, so one shared dependency
snapshot cannot create duplicate source failures. `risk_count` is exactly the
number of items in `items_by_section.risks`; `open_item_count` and
`authorization_required_count` include only current actionable items. Retained
expired, superseded, rolled-back, or validation-only history does not inflate them.

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
connectivity failure. Provider failures and partial coverage remain visible; the five
administrative reads select explicit direct policies without fallback, while direct
write expansion remains prohibited.

Every provider-routed canonical call contributes to these counters.
`search_entities`, `get_entity`, `list_areas`, `search_services`, and
`list_services` are attributed to `direct_ha_api`; they never claim
`standard_ha_mcp`. Entity search performs one provider request for its one
`GET /states` inventory. A truncated successful search increments success and
`partial_results`, not provider failures. Locally invalid domain or limit input
does not increment provider requests or failures. Fallback and prohibited-fallback
counters remain zero. Other transitional and direct-required exceptions are
likewise attributed to the provider actually used.
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

Beta 12 added a bounded `automation_reliability_analysis` health group; Beta 13
clarifies its aggregate and cache semantics:

- request, success, partial, and failure counts;
- cumulative finding and unique-root-cause counts by severity;
- traces and referenced entities examined;
- source failures and findings-truncated events;
- last successful analysis timestamp and last bounded failure category; and
- explicit `cache_supported: false` and `cache_status: not_configured` because no
  reliability-result cache exists.

One terminal analysis updates success, partial, or failure exactly once. Only a first
page updates finding/root-cause/source aggregates; cursor continuation does not count
the same population again. Provider
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

The Beta 15 canonical 25-tool classification is Native 8, Transitional 14, and
Deprecated 3 after Phase 3C capability-truth alignment, with 2 remaining planned
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

RC2dev8 closes the equivalent transport-layer gap. The authenticated gateway
captures at most the configured bounded MCP response, parses JSON or SSE
JSON-RPC, and reconciles the final audit outcome before writing it. Structured
Engineering `success=false` results use their exact known `error_code`;
FastMCP/Pydantic validation and unknown-tool results use `invalid_request`;
unclassified MCP execution errors use `internal_server_error`. Raw response
text, Pydantic trace detail, and caller values never enter the audit record.

The four fixed fail-closed legacy operations are answered before FastMCP
argument validation. Their audit records contain `result_status=failure`, the
canonical `provider_unavailable` or `provider_prohibited` code, an empty HA
endpoint category list, and no provider/fallback dispatch evidence.

For each future migration:

1. keep the public name and generated argument schema unchanged;
2. add behavior and failure regression tests first;
3. use `run_structured` and the shared response/error contracts;
4. route HA calls through observable clients;
5. add only safe audit resource identifiers and endpoint categories;
6. preserve destructive-service confirmation behavior; and
7. migrate one coherent read-only family before any write/governance work.

## Beta 13 reliability telemetry

Reliability health reports cumulative terminal requests, successes, partial results,
failures, finding severity counts, and separate unique-root-cause severity counts.
Finding/root-cause/source aggregates update on the first page only. Continuation
requests remain requests but do not duplicate the analysis population.

Reliability result caching is not configured. Health reports `cache_supported: false`,
`cache_counters_active: false`, and `cache_status: not_configured`; per-response cache
provenance says the same. No unsafe cache was added merely to produce hits.

The shared `home_assistant_ms` field is cumulative attempt effort. Additive timing
fields expose cumulative attempt effort, upstream wall-clock span, request count,
maximum concurrency, current request wall clock, and Engineering analysis wall clock.
The wall-clock span is measured from the first attempt start through the last attempt
completion and does not add overlapping durations.

## Beta 14 trace coverage and request-time telemetry

Trace coverage exposes only bounded counts and states: upstream returned, considered,
parsed, inside lookback, selected, details retrieved/failed, malformed starts/finishes,
duplicates, truncation, cutoff, and whether an empty result is trustworthy. It never
contains a run ID, automation identity, trace body, error text, or malformed payload.

One captured UTC analysis instant is used throughout a new analysis and its cursor
pages. First-page source, trace, finding, and root-cause aggregates update once.
Continuation pages update only their terminal request outcome and reuse a maximum-16,
five-minute sanitized public-output snapshot, so HA/provider requests and traces
examined do not repeat. Reusable reliability-result caching remains unsupported and
health continues to report `cache_supported: false`.

## Beta 15 change-impact telemetry

`change_impact_analysis` health reports identity-free cumulative request, terminal
success/partial/failure, operation, severity, direct/indirect, affected-object-type,
root-cause, dynamic-review, source-failure, truncation, cursor, and index-cache
counters. It also reports only the last successful timestamp and last bounded failure
category. Entity IDs, replacement IDs, affected-object IDs, states, configuration,
findings, evidence, traces, logs, templates, and secrets are excluded.

New analyses update terminal aggregates once. Cursor pages increment only request and
continuation counters and reuse a bounded sanitized five-minute snapshot; they do not
repeat provider access or count the same findings again. Response timing keeps current
analysis, index lookup/build provenance, evidence collection, cumulative HA effort,
HA wall-clock span, request count, and maximum concurrency distinct.
## Configuration integrity analysis telemetry

Beta 17 adds an identity-free `configuration_integrity_analysis` health group.
It tracks request/success/partial/failure outcomes; finding totals by severity,
type, and source; per-analysis unique source and target sums; orphan candidate,
dynamic-reference, manual-review, source-failure, and truncation events; cursor
continuations and failures; index cache outcomes; and bounded last-outcome
metadata.

`request_count` includes cursor pages. Terminal outcomes and all finding or
unique-object aggregates count new analyses only. Cursor failures are recorded
only in cursor counters. Validation failures add a failed request but no
findings. Pagination snapshots are bounded continuation state, not a general
result cache. Provider-routing counters record the Engineering provider only on
new evidence collection; continuation does not dispatch a provider.

Beta 18 changes classification precision, not counter semantics. Rejected dotted
non-entity text never becomes a finding and therefore does not increment finding,
severity, type, source, unique-target, or manual-review aggregates. Recognized
dynamic entity expressions remain counted separately and never inflate unique
target totals. No rejected token value is exposed through health telemetry.

## Incident correlation telemetry

Beta 19 adds an identity-free `incident_correlation` health group. It tracks
request, success, partial, and failure outcomes; hypotheses by confidence,
severity, and causal status; normalized events by type; per-analysis unique entity
and automation sums; manual-review, source-failure, evidence/timeline truncation,
cursor, index-cache, and bounded last-outcome counters. `result_cache_supported`
is always false.

`request_count` includes first pages and continuation pages. Terminal outcomes and
whole-analysis hypothesis/event/unique aggregates count a new analysis exactly
once. Cursor validation failures increment cursor-specific counters without
becoming failed analyses. Validation failures add a failed request but no evidence
aggregate. When index context is not requested, no index hit or miss is recorded.
Provider routing attributes orchestration to `engineering` and approved evidence
reads to their actual provider; no Standard HA MCP success or fallback is claimed.

Beta 20 narrows `source_failures` to actual failed sources or bounded failed source
operations. A successful partial dependency index, unsupported source type,
not-requested source, retention limit, truncation, or dynamic-reference uncertainty
does not increment source or provider failure counters. Such an analysis may still
increment `partial_count`, and provider routing may record a successful partial
result. Actual index errors increment `failures_by_provider.engineering`; timeouts
retain `provider_timeout`, and usable item-level failures retain
`item_read_failure`. Cursor continuations preserve the frozen source semantics and
do not repeat terminal, source-failure, hypothesis, or event aggregates.

## RC2dev5 classified outcomes and freshness

`provider_operational_failures`, `domain_outcome_counts`,
`validation_error_counts`, `authorization_error_counts`, and
`cursor_error_counts` prevent expected client/domain outcomes from degrading
provider or analysis health. Expected source misses use explicit
`domain_outcome_*` failure categories. The dependency index reports independent
build state, freshness, validity reason, soft/hard expiry, evidence age, timing,
generation, fingerprint, progress, and a per-operation build profile. A
soft-expired generation is labeled stale and returned while one background
refresh runs; hard-expired or invalidated evidence is unavailable.

Dashboard reachability is timestamped and ages to `unknown`; it is never
inferred indefinitely from a prior success. Exact dashboard not-found is a
domain outcome and leaves provider reachability and contract state intact.
Beta/RC prewarming is enabled by default after a 45-second delay, first checks
Home Assistant connectivity, shares the index single-flight path, and retries no
faster than 300 seconds. Attempt count, timestamps, next retry, and bounded
failure category are exposed without blocking startup.
