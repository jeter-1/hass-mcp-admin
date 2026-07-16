# HA MCP Engineering Server RC3A release notes

## Stage identity

- Development version: `2.0.0-rc2-dev1`
- Proposed release tag: `v2.0.0-rc2-dev1`
- Generic image: `ghcr.io/jeter-1/hass-mcp-engineering-beta`
- Proposed version image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev1`
- Proposed source image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-<accepted-commit>`
- Rollback image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`

`2.0.0-rc2-dev1` is an intentionally non-final RC3 development version.
AwesomeVersion 25.8.0, matching Home Assistant 2026.7.2, orders it newer than
`2.0.0-rc.2` and older than final `2.0.0-rc.3`. The rejected
`2.0.0-rc.2.rc3a.1` form is syntactically valid but incomparable under that
library and must never be tagged or published. RC3A, RC3B, and RC3C must
complete before the final RC3 version is created.

No RC3A tag or image is created by the implementation pull request. Pull-request
CI validates images without pushing them.

## Purpose

RC3A adds a read-only dashboard evidence provider for the installed
`homeassistant-ai/ha-mcp` server. It does not make the generic
`StandardHaMcpGateway` available. The two provider identities remain separate:

- `standard_ha_mcp`: unavailable;
- `upstream_dashboard`: optional, explicitly configured, dashboard-only, and
  read-only.

The registered tool count increases from 38 to 40. The 25 canonical tools,
their schemas and classifications, zero planned-capability count, schema
version 1, governance behavior, external approval authority version 2, direct
Home Assistant policies, and production v1.1.2 runtime remain unchanged.

## Configuration

The Engineering Beta add-on adds one password-style option:

```yaml
upstream_dashboard_mcp_url: ""
```

The operator supplies the complete internal streamable-HTTP URL, including its
secret-bearing path, in Home Assistant add-on configuration. Empty means
unconfigured and does not prevent server startup. RC3A does not request
Supervisor discovery permissions and does not discover the upstream add-on.

The value is a secret. Never paste it into source, issue text, pull-request
text, logs, screenshots, acceptance evidence, or support output. Diagnostics
may report only configured state, credential presence, sanitized identity,
reachability, schema state, timing, and bounded error categories.

## Dashboard-only MCP boundary

The maintained `mcp==1.9.0` dependency already pinned by the repository supplies
the streamable-HTTP client. Each operation opens a bounded session, performs
`initialize`, completes MCP initialization, obtains the bounded `tools/list`
catalog, validates the required capability, optionally calls one fixed read
tool, and closes the session. A later call opens a new session, allowing
reconnection after an upstream restart.

The adapter allowlist contains exactly:

```text
ha_config_get_dashboard
```

Permitted argument shapes are fixed:

- inventory: `url_path=null`, `list_only=true`;
- exact read: exact `url_path`, `list_only=false`, and optional
  `force_reload`.

There is no public or internal arbitrary-tool forwarding API. Set/delete
dashboard, backup, service, reload, automation write, physical-action, and
unknown tool names are rejected before transport dispatch.

## Capability discovery

Every dashboard operation records the live sanitized MCP identity and validates
the upstream capability contract before tool dispatch:

- actual sanitized initialize server name and version;
- negotiated MCP protocol version;
- bounded upstream tool catalog;
- presence of `ha_config_get_dashboard`;
- string-capable `url_path`;
- Boolean `list_only`;
- no unknown required arguments;
- read-only annotation and no destructive annotation;
- optional Boolean `force_reload` support.

Additional optional arguments do not make a compatible upstream unavailable.
A changed required type, unknown required argument, missing read-only hint, or
destructive hint fails closed. RC3A records SHA-256 fingerprints for the
required schema and bounded catalog but does not expose raw schemas in health.

The implementation does not hard-code or pin the deployed upstream server
identity. A differently named operator-configured endpoint is accepted when the
required tool, schema, and read-only annotations are compatible. Live
acceptance records the sanitized identity returned by `initialize`; identity
pinning is an explicit RC3B decision.

## New read-only tools

### `list_dashboards`

Input:

```json
{
  "limit": 100
}
```

`limit` is an integer from 1 through 200. The tool returns deterministic,
bounded storage-mode dashboard metadata when present upstream: canonical URL
path, stable ID, title, icon, sidebar visibility, administrative restriction,
and storage/mode indicators. It does not invent missing fields. It reports
`truncated=true` and partial completeness when the requested or response bound
omits otherwise valid results.

### `get_dashboard_config`

Input:

```json
{
  "url_path": "lovelace",
  "force_reload": true
}
```

The URL path must be an exact lowercase canonical path containing only letters,
digits, underscore, or hyphen. Titles, fuzzy matching, slashes, queries, and
arbitrary upstream arguments are rejected locally. The tool returns the
sanitized configuration as untrusted data, the canonicalized path returned by
upstream when valid, two explicit hashes, source metadata, and completeness:

- `config_hash` is the authoritative upstream-compatible optimistic-lock value.
  Engineering independently serializes the complete raw configuration with
  sorted keys, compact separators, and default JSON ASCII escaping, computes
  SHA-256, takes the first 16 lowercase hexadecimal characters, and requires an
  exact match with the upstream-supplied value. Missing, malformed, or
  mismatched upstream values fail closed as `invalid_response`.
- `engineering_config_hash` is a full 64-character lowercase SHA-256 evidence
  fingerprint. It uses sorted keys, compact separators, UTF-8 JSON with
  `ensure_ascii=false`, and strict JSON-compatible values. It is computed from
  the complete raw configuration before sanitation or omission and is not an
  optimistic-lock token.

Example bounded data:

```json
{
  "url_path": "lovelace",
  "configuration": {"views": []},
  "config_hash": "dbbb0f164dc1cb81",
  "engineering_config_hash": "dbbb0f164dc1cb81872f523bc78bcd517993dea55a9d4f97fc8f854795bc521e",
  "configuration_returned": true
}
```

When a configuration cannot fit the Engineering response limit, RC3A returns a
structured `response_too_large` failure with both verified hashes, estimated
size, configured response limit, and `configuration_returned=false`. It never
hashes sanitized or truncated JSON. When the lower MCP transport limit prevents
receipt of the complete configuration, neither hash is claimed.

## Health and errors

`get_server_health` adds an `upstream_dashboard` section containing only bounded
diagnostics:

- configured and credential-present state;
- reachable and capability status;
- sanitized server name/version and MCP protocol version;
- tool count, required-tool presence, and schema compatibility;
- required-schema and catalog fingerprints;
- last successful handshake and dashboard-call timestamps;
- connection/tool-call latency;
- request, success, timeout, reconnect, and categorized failure counts;
- bounded session state;
- allowlisted-tool count and `writes_allowed=false`.

Stable failure categories are:

- `not_configured`
- `authentication_failed`
- `endpoint_rejected`
- `connection_failed`
- `timeout`
- `protocol_error`
- `invalid_response`
- `required_tool_missing`
- `schema_incompatible`
- `upstream_error`
- `response_too_large`
- `internal_error`

Errors contain a safe provider identity, retryability, completeness, request
ID, timing, and whether upstream dispatch occurred. Raw exception text,
endpoint URL, secret path, authentication headers, schemas, and upstream bodies
are excluded.

## Security boundary

RC3A performs no dashboard write and grants no new Home Assistant write
authority. Specifically:

- `ha_config_set_dashboard` cannot dispatch;
- `ha_config_delete_dashboard` cannot dispatch;
- `ha_manage_backup` cannot dispatch;
- service execution and reload cannot route through `upstream_dashboard`;
- automation upsert/apply/rollback cannot route through it;
- physical actions cannot route through it;
- existing direct-HA exceptions and policies are unchanged;
- `StandardHaMcpGateway.available` remains false;
- upstream dashboard titles, card text, configuration, warnings, and attributes
  are inert untrusted data, never instructions or authorization.

The endpoint, hostname, port, secret path, query, user information, and derived
credential fragments are retained only for transport/redaction use and never
serialized. URL-bearing MCP and HTTP client loggers are suppressed because the
pinned client otherwise logs full streamable-HTTP URLs.

## Known limitations

- Only storage-mode dashboards surfaced by `ha_config_get_dashboard` are in
  scope.
- No dashboard creation, update, deletion, backup, rollback, screenshot, or
  change-plan path exists.
- No Supervisor discovery is performed.
- Sessions are intentionally short-lived; RC3A favors safe closure and restart
  recovery over connection pooling.
- Live upstream identity, schema, and latency evidence cannot be claimed until
  post-deployment acceptance.
- RC3A records but does not pin the observed MCP server identity; RC3B must
  decide whether identity or implementation-family pinning is warranted.
- A configured but unavailable provider does not prevent the Engineering server
  or existing tools from starting.

Deployment and operational evidence are defined in
[`RC3A_ACCEPTANCE.md`](RC3A_ACCEPTANCE.md).
