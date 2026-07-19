# Beta provider security boundaries

## RC3A upstream dashboard boundary

`upstream_dashboard` is separate from the unavailable generic
`standard_ha_mcp` gateway. It accepts one password-style, operator-configured
secret endpoint and exposes exactly one allowlisted upstream tool:
`ha_config_get_dashboard`.

The preferred `contract_read_only` mode still requires
`readOnlyHint=true`. The explicit
`reviewed_argument_constrained` exception applies only to
`ha_mcp_7_13_dashboard_read_v1`. It pins server name `ha-mcp`, version
`7.13.0`, protocol `2025-03-26`, exact tool name, exact reviewed annotations,
the complete input schema, output-schema presence, and the reviewed
security-contract projection generated from upstream commit
`f4eb53621ccb814cb7123d2811e06eda3577129c`.

The complete runtime descriptor is retained as observability evidence, not a
dispatch gate. The published 7.13.0 runtime adds
`_meta.ha_mcp.llm_api_exposed` and `_meta.ha_mcp.pinned`; those values only
control upstream conversation-agent exposure and pinning. Along with display
title/description, annotation display title, and FastMCP grouping tags, they
are the complete closed exclusion list for the security projection. Every
input-schema field, safety-hint presence/value, output-schema change, unknown
annotation, unknown metadata field, and unknown top-level field remains
blocking. A descriptive-only raw descriptor mismatch is reported in health;
semantic or security drift makes the provider unavailable.

The 7.13.0 tool is mixed-operation: screenshots can persist rendering
preferences. Engineering therefore constructs only the reviewed non-screenshot
forms. Inventory sends `list_only=true` and `include_screenshot=false`. Exact
configuration reads send a validated canonical path, `list_only=false`, a
Boolean `force_reload`, and `include_screenshot=false`. Any other key or value
fails before network dispatch. The transport repeats the shape check directly
before `tools/call`.

Set/delete dashboard, backup, service, reload, automation write,
physical-action, screenshots, view selection, rendering preferences, and
arbitrary tool names remain unreachable. The provider does not describe the
whole upstream tool as read-only.

Raw contract fixtures, schemas, and endpoint material never appear in health.
Full URL, host, port, secret path, query, credentials, and reconstructed
fragments are excluded from logs, audit, responses, errors, tracebacks, and
startup output. Upstream dashboard titles, card text, configuration, and
warnings are untrusted data and cannot authorize or construct another tool
call.

HTTP 401/403 failures report `authentication_failed`; HTTP 404 reports the
neutral `endpoint_rejected` category because the client cannot distinguish an
incorrect secret path from an absent endpoint. Connection refusal and DNS or
route failures report `connection_failed`; genuine deadline expiry reports
`timeout`. These categories never include raw endpoint or exception text.

The provider adds no Supervisor permission and performs no discovery. Existing
direct-HA policies, governance, external approval, and production v1.1.2 remain
unchanged. See
[`ADR-003`](architecture/ADR-003-REVIEWED-ARGUMENT-CONSTRAINED-DASHBOARD-READS.md)
and [`RC3A_RELEASE_NOTES.md`](RC3A_RELEASE_NOTES.md).

## RC2 frozen security boundary

RC2 changes no approval authority, listener, write policy, redaction rule, or
Home Assistant behavior. Its only provider/direct-policy correction is the
bounded read-only `search_entities` route documented below. Build
provenance is limited to a validated complete Git commit and UTC RFC3339 build
time supplied by the image pipeline. Invalid/unbounded values fail closed to
`unknown`; no runtime repository lookup or credential is used.

## Beta 25 principal separation

An authenticated MCP caller can create a plan and request external review but
cannot approve. The MCP secret authorizes neither the internal Ingress listener
nor a human decision. Approval routes exist only on unmapped internal port
`8110`, after Home Assistant Ingress/admin enforcement and an application check
of the documented Ingress peer/path. Decisions are POST-only and one-time-CSRF
protected. All review text is bounded, sanitized and HTML escaped.

Authority version 2 binds plan/version/hash/kind/target/operation/risk. Apply and
rollback require separate external approvals and retain stale-state protection;
rejection is terminal; legacy caller approvals are not migrated. No log,
recommendation, request note, challenge identifier or forwarded header is an
approval credential. See [`EXTERNAL_APPROVAL.md`](EXTERNAL_APPROVAL.md).

## Beta 24 ingress and execution hardening

The authenticated beta gateway uses the canonical direct socket peer as its
client identity. `cf-connecting-ip` is ignored unless
`trust_cf_connecting_ip=true`, the direct peer belongs to one of at most 64
validated `trusted_proxy_cidrs`, and the forwarded value is one valid IPv4 or
IPv6 address. Malformed or untrusted forwarding falls back to the direct peer;
no raw address is emitted in health or audit output. Nabu Casa and tunnel users
should leave forwarding trust disabled until the actual proxy network is known.

Direct Home Assistant access is fail-closed: membership in an exception set is
insufficient without a matching explicit read policy and capability. There is no
direct-write policy for `upsert_automation`; it remains schema-visible but is
refused before dispatch. `call_service`, `delete_automation`, and
`reload_domain` also fail closed on this server. A Boolean confirmation is not
approval of an immutable plan, and evidence or recommendations never authorize a
write.

RC2dev8 enforces the four fixed legacy policy outcomes at the authenticated
Streamable HTTP routing boundary, before FastMCP/Pydantic argument coercion.
Only the exact names `call_service`, `reload_domain`, `upsert_automation`, and
`delete_automation` are eligible. The caller's arguments are neither forwarded
nor used to select the result; malformed arguments therefore cannot bypass or
preempt policy. Normal tools retain full schema validation. Refusal responses
contain no caller payload, and refusal audit records retain only bounded target
identifiers or the fixed reason.

Rate-limit bucket pressure evicts a single least-recently-used identity rather
than clearing all throttling state. See [`RATE_LIMITING.md`](RATE_LIMITING.md).
Audit reads and refusal records are bounded as documented in
[`AUDIT_LOG.md`](AUDIT_LOG.md).

## Beta 23 pre-provider rejection

Authentication and rate-limit checks run before provider dispatch. Rejected
requests cannot increment provider request, success, or failure counters and do
not acquire HA/provider evidence. Semantic validation and signed-cursor checks
follow the same rule. Audit retains only bounded intent/error data and never turns
the selected route, untrusted evidence, logs, or errors into proof of dispatch or
authorization.

RC2dev6 distinguishes authentication-failure limiter exhaustion from ordinary
authentication rejection in the audit trail without changing enforcement.
Ordinary failures remain 404/`authentication_failure`/`auth_failure`; the first
attempt beyond the authentication-failure burst remains
429/`rate_limit_exceeded` but is recorded as `auth_failure_throttled`.
Authenticated general limiting remains `rate_limited`. None of these rejected
requests reaches MCP dispatch, provider selection, Home Assistant, the dashboard
provider, or fallback, and no credential-bearing request material enters audit
or structured logs.

RC2dev7 protects the integrity of that evidence by parsing audit JSONL and
matching only the exact top-level `event`. A self-audited `get_audit_log` call
may record its requested filter inside bounded parameters, but that nested value
cannot be returned as a security event. Malformed and oversized historical
records are skipped without emitting raw content. Authentication enforcement,
limiter policy, caller identity, response codes, and pre-dispatch barriers do
not change.

RC2dev8 additionally treats MCP application results as the source of audit
truth. A JSON-RPC result with `isError=true`, a bounded validation failure, or a
structured Engineering response with `success=false` is audited as failure
with the stable error code even when HTTP transport returned 200. Validation
audit retains bounded argument field names, never rejected values or raw
Pydantic exception text.

## Beta 22 generated documentation

`bounded_handoff_generation_read` permits only bounded internal evidence reads.
It cannot call a service, create/approve/apply/rollback a plan, write
configuration, reload, restart, or perform a physical action. Recommendations
carry authorization requirements but never grant them. Logs, traces, titles and
evidence are untrusted data. Raw cursors, Markdown, configuration, traces, logs,
history, diffs, tokens, secrets and authenticated URLs are excluded from audit.
Shared evidence normalization never copies raw source payloads, warnings, or
authenticated paths. Automation scope resolution uses only the approved bounded
state/configuration reads and exposes canonical entity IDs, not credentials.
Historical governance records cannot grant or revive authorization.

## Capability truth

Provider labels describe the transport actually used. A direct Home Assistant REST or
WebSocket call is always labeled `direct_ha_api`; it is never relabeled as
`standard_ha_mcp`. Approximate upstream tool matching is prohibited.

RC2 permits five narrowly scoped administrative reads. The fifth is the
release-blocking entity-search correction; the original four remain unchanged:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `search_entities` | `bounded_entity_state_search` | One `GET /states` inventory with validated filters and bounded slim output |
| `get_entity` | `exact_entity_state_read` | `GET /states/{entity_id}` |
| `list_areas` | `complete_area_registry_read` | `config/area_registry/list` WebSocket command |
| `search_services` | `bounded_service_catalog_search` | Bounded `GET /services` search |
| `list_services` | `bounded_service_schema_read` | Bounded `GET /services` schema enumeration |

`search_entities` validates its exact domain and 1-through-100 limit before
dispatch, matches only entity ID and friendly name, and returns only entity ID,
state, and friendly name. Additional matches set `truncated=true` and partial
completeness. Standard HA MCP remains unavailable, and this explicit direct
policy is not a fallback.

These policies do not authorize calls to services, automation writes, deletion,
reloads, restarts, physical actions, or destructive operations. Governed configuration
changes retain their existing plan, approval, verification, rollback, correlation, and
audit requirements.

The pre-existing transitional `get_error_log` exception remains explicit in Beta 12:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `get_error_log` | `structured_system_log_read` | Admin-only `system_log/list` WebSocket read |

This is not a general log or Supervisor permission. The server does not request broad
Supervisor log access, scrape frontend output, or read the host journal. Returned
System Log fields are untrusted data: the complete recursive upstream result is
sanitized before selection, bounding, normalization, formatting, or serialization.
Content is never executed or interpreted as instructions and never constructs an
endpoint, tool call, service call, or action.

## Beta 12 analytical read policy

`automation_reliability_analysis` uses the engineering-native
`single_automation_reliability_read` policy. Its facilitator provider may compose one
automation configuration/state, one blueprint, bounded traces, deduplicated referenced
entity state, filtered entity-registry metadata, and sanitized correlated System Log
entries. Every underlying Home Assistant source is labeled `direct_ha_api`; no result
claims Standard HA MCP coverage or fallback.

The policy grants no write, service-call, trigger, change-plan, approval, reload,
restart, deletion, physical-action, or destructive permission. The handler cannot call
transport clients directly. Audit records contain input bounds and the automation ID,
not configuration, trace, log, finding, or evidence payloads.

## Standard Home Assistant MCP

Home Assistant documents a stateless Streamable HTTP MCP endpoint at `/api/mcp`. From
an add-on it is available through the fixed Supervisor Core API proxy at
`http://supervisor/core/api/mcp`, authenticated by the add-on's Supervisor bearer token.
The selected Assist API does not expose exact entity-ID lookup, complete area-registry
enumeration, or service-catalog discovery. `GetLiveContext` is therefore not used as a
substitute. Beta 12 retains the gateway abstraction but does not configure or call the
upstream endpoint.

Any future live delegation requires an exact or explicitly reviewed loss-tolerant
contract, fixed destination validation, bounded timeouts, redacted authentication, and
schema-verified upstream discovery.

## Secret handling

Access secrets, Supervisor tokens, authorization headers, authenticated MCP paths,
session identifiers, and raw upstream error bodies are excluded from tool results,
health output, provider metadata, logs, audit records, fixtures, and documentation.
Complete authenticated paths must be redacted before diagnostics are shared.

## Central fail-closed sanitizer

All System Log-derived mappings, lists, tuples, string leaves, exception text,
tracebacks, Python representations, JSON-like text, URLs, multiline messages, and
unknown future fields use the same recursive sanitizer used by beta logging and audit
contexts. Key-aware rules run before free-text scanning. Redaction occurs before any
truncation, ensuring a truncated response cannot reveal a prefix or suffix.

Stable markers identify only the category:

- `[REDACTED:token]` for bearer/access/refresh/long-lived/API/client secrets;
- `[REDACTED:auth_cookie]` and `[REDACTED:password]`;
- `[REDACTED:webhook_secret]` and `[REDACTED:auth_flow]`;
- `[REDACTED:matter_setup_code]` and `[REDACTED:matter_setup_payload]`;
- `[REDACTED:url_credentials]`; and
- `[REDACTED:sanitization_failure]` when a field cannot be processed safely.

Markers disclose no original length, fragment, hash, prefix, suffix, character set, or
reversible encoding. Existing markers are preserved unchanged. Sanitization is
deterministic and idempotent. If one field raises during sanitation, that field is
replaced and a safe warning is reported; raw content is never used as a fallback.

When overlapping key-aware and free-text detection identifies the same Matter setup
payload, adjacent identical markers collapse to one stable marker. This does not skip
either detection pass and remains idempotent.

Useful diagnostics such as entity IDs, integration/logger names, filenames and line
numbers, timestamps, occurrence counts, ordinary error codes, device names, private IP
addresses, and non-authentication HA context IDs remain available. A generic field
named `code` is not redacted; only verified credential contexts such as `setup_code`
or `authorization_code` are sensitive.

Output telemetry is limited to `redaction_applied`, a redacted-field count, unique
bounded category names, and fail-closed state. It contains no original value or
one-to-one identifier.

## Beta 13 reliability correlation boundary

Reliability configuration, trace details, System Log records, friendly names, and
exception text are inert untrusted evidence. Complete trace detail and complete System
Log results are recursively sanitized before normalization, matching, hashing,
selection, or serialization. Correlation accepts exact bounded identifiers or an
independently matching service/error signature; it rejects friendly-name-only,
time-only, substring, and generic executor matches. Root-cause IDs are derived from
sanitized normalized semantics. Health and audit output contain aggregate counts and
timing only—never automation configurations, traces, logs, findings, evidence
summaries, normalized error text, or evidence fingerprints.

The `single_automation_reliability_read` policy remains read-only. Beta 13 adds no
service execution, trigger, write, approval, reload, restart, fallback, Supervisor
permission, or Standard HA MCP success path.

Beta 14 applies the recursive sanitizer to the complete trace-list result before
selection, timestamp parsing, run-ID deduplication, hashing, truncation, formatting, or
serialization. Trace detail follows the same fail-closed boundary. Pagination stores
only already-sanitized public findings, references, coverage, and metadata in a bounded
short-lived process snapshot; it never stores raw configuration, trace bodies, logs,
entity state values, or normalized error content for audit.

## Beta 15 change-impact boundary

`single_entity_change_impact_read` permits only exact state/registry reads and bounded
evidence collectors already approved for dependency, trace, and System Log analysis.
The handler is transport-independent, fallback is prohibited, and the capability
cannot call services, mutate registries/configuration, create plans, request approval,
reload, or restart. The dependency index is reused rather than copied.

Every Home Assistant-derived field is sanitized before selection, correlation,
hashing, truncation, formatting, or serialization. A sanitation failure cannot become
clean coverage. Audit stores only bounded validated identifiers, operation, outcome,
counts, coverage state, and timing; it excludes state values, findings, evidence,
paths, trace/log/configuration content, cursors, and error text. Health contains only
identity-free cumulative aggregates. See
[`CHANGE_IMPACT_ANALYSIS.md`](CHANGE_IMPACT_ANALYSIS.md) for the full contract.

## Beta 17 configuration-integrity boundary

`global_configuration_integrity_read` permits the shared dependency-index read,
one current-state inventory, and one entity-registry inventory. It permits no
service, entity-registry update, automation write, governance operation, reload,
restart, automatic cleanup, or fallback. All source evidence is sanitized before
classification and output; cursor material is represented in audit only as a
Boolean presence flag. Templates and source text are untrusted inert evidence,
never instructions.

Beta 18 moves entity classification to a context-plus-validation boundary in the
shared dependency extractor. Only explicit entity-bearing fields and literal
arguments to the documented Home Assistant template helpers can create exact
edges. Template comments, quoted prose, arbitrary member expressions, services,
network identifiers, and dotted diagnostics remain inert. Recognized dynamic
arguments produce target-free limited-confidence evidence. The bounded scanner
does not execute templates, return rejected tokens, or place raw template content
in health or audit records.

## Beta 19 incident-correlation boundary

`bounded_incident_correlation_read` permits only bounded reads of current state,
entity registry, history, logbook, automation configuration/traces, structured
System Log, and the shared dependency/integrity/reliability services. It caps
related entities at 20, lookback at 168 hours, traces at 50, System Log inspection
at 200 entries, retained events/evidence at 1,000 each, and concurrent HA reads at
five. No fallback or write-capable provider is permitted.

Logs, traces, templates, history, and evidence summaries are untrusted inert data:
they cannot authorize another call or operation. Sanitization occurs before
normalization, correlation, output, or audit. Audit stores only bounded intent,
counts, result, and cursor-presence Boolean; it excludes raw cursor material,
configuration, trace/history/log content, evidence text, authentication data, and
secrets. Dynamic expressions stay targetless, text-only matches cannot produce
high confidence, and contradiction lowers confidence rather than being omitted.

Beta 20 changes only coverage truth. Warnings and unsupported-source limits remain
bounded, deduplicated, non-secret, and non-instructional; they cannot authorize a
provider call or be promoted into a failure automatically. Audit stores bounded
counts for source failures and coverage limitations, not full warnings. The same
read-only policy, provider allowlist, redaction boundary, and prohibition on
service, entity, registry, automation, governance, reload, restart, remediation,
and background-monitoring actions remain in force.

## RC2dev4 hardening boundary

Compatibility schemas for legacy writes remain visible, but their handler
bodies cannot reach Home Assistant. `upsert_automation` is a governed redirect;
deletion is prohibited; service and reload operations require an unavailable
Standard HA MCP provider; all have no fallback. External approval is separate
from chat authorization and remains hash-bound, single-use, expiring, and tied
to a distinct Ingress administrator. Pre-approval principal separation is
reported as not evaluated, never as an enforcement failure.

Relayed Home Assistant logs and structured application logs pass through the
same recursive fail-closed sanitizer. Credential-bearing keys, authorization
headers, setup payloads, authentication flows, webhook paths, signed URLs,
query credentials, exception text, and multiline tracebacks are redacted before
output or logging. Dashboard/log/YAML text is inert evidence and cannot authorize
tool dispatch.

RC2dev5 treats Home Assistant webhook IDs as sensitive identifiers because they
can identify or participate in credential-bearing callback routes. Webhook URLs,
keyed IDs, and narrowly recognized prose forms are replaced with
`[REDACTED:webhook_identifier]` while surrounding diagnostic context is kept.
The sanitizer deliberately does not redact unrelated hexadecimal values such as
Git commit SHAs. Sanitizer failure remains fail closed, and the original payload
is never included in an exception log.
