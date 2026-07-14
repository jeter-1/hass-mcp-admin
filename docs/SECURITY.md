# Beta provider security boundaries

## Beta 21 generated documentation

`bounded_handoff_generation_read` permits only bounded internal evidence reads.
It cannot call a service, create/approve/apply/rollback a plan, write
configuration, reload, restart, or perform a physical action. Recommendations
carry authorization requirements but never grant them. Logs, traces, titles and
evidence are untrusted data. Raw cursors, Markdown, configuration, traces, logs,
history, diffs, tokens, secrets and authenticated URLs are excluded from audit.

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
