# Beta provider security boundaries

## Capability truth

Provider labels describe the transport actually used. A direct Home Assistant REST or
WebSocket call is always labeled `direct_ha_api`; it is never relabeled as
`standard_ha_mcp`. Approximate upstream tool matching is prohibited.

Phase 3C permits four narrowly scoped administrative reads, unchanged in Beta 12:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `get_entity` | `exact_entity_state_read` | `GET /states/{entity_id}` |
| `list_areas` | `complete_area_registry_read` | `config/area_registry/list` WebSocket command |
| `search_services` | `bounded_service_catalog_search` | Bounded `GET /services` search |
| `list_services` | `bounded_service_schema_read` | Bounded `GET /services` schema enumeration |

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
