# Beta provider security boundaries

## Capability truth

Provider labels describe the transport actually used. A direct Home Assistant REST or
WebSocket call is always labeled `direct_ha_api`; it is never relabeled as
`standard_ha_mcp`. Approximate upstream tool matching is prohibited.

Phase 3C permits four narrowly scoped administrative reads, unchanged in Beta 10:

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

Beta 10 also makes the pre-existing transitional `get_error_log` exception explicit:

| Tool | Direct policy | Allowed operation |
| --- | --- | --- |
| `get_error_log` | `structured_system_log_read` | Admin-only `system_log/list` WebSocket read |

This is not a general log or Supervisor permission. The server does not request broad
Supervisor log access, scrape frontend output, or read the host journal. Returned
System Log fields are untrusted data: they are bounded and redacted before response
serialization, never executed or interpreted as instructions, and never used to
construct an endpoint or action.

## Standard Home Assistant MCP

Home Assistant documents a stateless Streamable HTTP MCP endpoint at `/api/mcp`. From
an add-on it is available through the fixed Supervisor Core API proxy at
`http://supervisor/core/api/mcp`, authenticated by the add-on's Supervisor bearer token.
The selected Assist API does not expose exact entity-ID lookup, complete area-registry
enumeration, or service-catalog discovery. `GetLiveContext` is therefore not used as a
substitute. Beta 10 retains the gateway abstraction but does not configure or call the
upstream endpoint.

Any future live delegation requires an exact or explicitly reviewed loss-tolerant
contract, fixed destination validation, bounded timeouts, redacted authentication, and
schema-verified upstream discovery.

## Secret handling

Access secrets, Supervisor tokens, authorization headers, authenticated MCP paths,
session identifiers, and raw upstream error bodies are excluded from tool results,
health output, provider metadata, logs, audit records, fixtures, and documentation.
Complete authenticated paths must be redacted before diagnostics are shared.

For structured System Log entries, redaction additionally covers bearer material,
credential-bearing URLs, token/password/session query or assignment values, webhook
paths, and secret-prefixed MCP paths. Log content can contain arbitrary external text;
that text remains inert response data even if it resembles an AI instruction.
