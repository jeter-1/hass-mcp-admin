# HA MCP Engineering Server RC3A release notes

## Release state

- Advertised version before successful promotion: `2.0.0-rc2-dev1`
- Automated promotion target: `2.0.0-rc2-dev2`
- Automated tag: `v2.0.0-rc2-dev2`
- Promoted image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev2`
- Rollback image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev1`

The feature PR intentionally does not change Home Assistant add-on metadata to
dev2. After merge, the promotion workflow consumes
`.release/next-version`, creates a local release commit, publishes and
anonymously verifies the multi-architecture image, and only then atomically
pushes the release commit and annotated tag. A failed publication leaves the
advertised add-on version at dev1.

AwesomeVersion 25.8.0 orders dev2 newer than dev1 and below final
`2.0.0-rc.3`.

## Reviewed argument-constrained dashboard reads

Upstream `homeassistant-ai/ha-mcp` 7.13.0 changed
`ha_config_get_dashboard` into a mixed-operation tool. Its ordinary
configuration, list, and search paths remain reads, but
`include_screenshot=true` can persist rendering preferences. The tool therefore
no longer publishes `readOnlyHint=true`.

RC3A does not treat the whole upstream tool as read-only. It uses the explicit
trust mode:

```text
reviewed_argument_constrained
```

with profile:

```text
ha_mcp_7_13_dashboard_read_v1
```

The reviewed upstream source is commit
`f4eb53621ccb814cb7123d2811e06eda3577129c`. The profile pins:

- server name `ha-mcp`;
- server version `7.13.0`;
- MCP protocol `2025-03-26`;
- tool name `ha_config_get_dashboard`;
- the complete reviewed tool contract;
- the exact reviewed annotations;
- exact Engineering-owned invocation builders.

The expected input-schema fingerprint is:

```text
7f2b6a086faec129c182fe6f791722beda9fffc659a507f55a3b20d72e2155a6
```

The complete reviewed contract fingerprint is:

```text
170c2aac1d6437d5c42b7f1d48f5322fef4736c414654c4cc4f7830138e959ca
```

The contract fingerprint is SHA-256 over the complete JSON-compatible
`tools/list` tool object, serialized as UTF-8 JSON with sorted object keys,
compact separators, `ensure_ascii=false`, and no coercion such as
`default=str`. It covers the tool name, description/title, input schema,
annotations, output schema when present, and FastMCP metadata present in the
reviewed fixture. Dictionary order does not affect it; any semantic or metadata
drift in the reviewed object does.

The normal `contract_read_only` mode remains separate and continues requiring
`readOnlyHint=true`. A missing read-only hint does not by itself select the
reviewed profile.

## Exact allowed upstream calls

Inventory constructs exactly:

```json
{
  "list_only": true,
  "include_screenshot": false
}
```

Exact configuration reads construct exactly:

```json
{
  "url_path": "<validated-canonical-path>",
  "list_only": false,
  "force_reload": true,
  "include_screenshot": false
}
```

`force_reload=false` is the only permitted variation.

Engineering cannot construct or forward `include_screenshot=true`,
`view_path`, `full_page`, theme, dark-mode, dimensions, rendering preferences,
unknown optional arguments, raw caller-supplied MCP arguments, or another tool
name. Prohibited arguments fail before network dispatch.

`ha_config_set_dashboard`, `ha_config_delete_dashboard`, backup, service,
reload, automation-write, and physical-action tools remain disconnected from
this provider. `StandardHaMcpGateway.available` remains false.

## Health truth

Successful reviewed validation reports bounded fields including:

```json
{
  "capability_status": "available",
  "trust_mode": "reviewed_argument_constrained",
  "trust_profile": "ha_mcp_7_13_dashboard_read_v1",
  "pinned_server_name": "ha-mcp",
  "pinned_server_version": "7.13.0",
  "reviewed_contract_match": true,
  "argument_constraints_active": true,
  "screenshots_allowed": false,
  "preference_writes_allowed": false,
  "writes_allowed": false
}
```

Identity, version, protocol, schema, contract, annotation, argument, and hash
drift fail closed under stable bounded categories. Health never exposes the
endpoint, port, secret path, credentials, complete schema, fixture, raw
description, or raw exception.

## Hash contract

The 7.13.0 dashboard hash implementation is unchanged:

- compact JSON;
- sorted keys;
- default ASCII escaping;
- SHA-256;
- first 16 lowercase hexadecimal characters.

Engineering requires the upstream `config_hash`, independently recomputes it
from the complete raw configuration, and requires exact equality.
`engineering_config_hash` remains a separate 64-character SHA-256 over compact,
sorted, UTF-8 JSON with `ensure_ascii=false`. Both hashes are calculated before
sanitization or response omission. Missing, malformed, mismatched, non-JSON, or
incomplete data fails closed.

## Compatibility boundary

The release preserves:

- 40 registered tools;
- 25 canonical tools;
- zero planned capabilities;
- schema version 1;
- both RC3A public input schemas;
- automation governance and external approval;
- audit persistence and redaction;
- existing direct Home Assistant policies;
- production v1.1.2.

Any upstream version or reviewed contract change requires a new explicit
review. A future pure-read upstream dashboard tool with
`readOnlyHint=true` should replace this exception.
