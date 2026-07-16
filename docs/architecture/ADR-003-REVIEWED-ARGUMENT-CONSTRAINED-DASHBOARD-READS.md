# ADR-003: Reviewed argument-constrained upstream dashboard reads

## Status

Accepted for the RC3A single-operator development release.

## Context

RC3A originally required the upstream `ha_config_get_dashboard` tool to declare
`readOnlyHint=true`. `homeassistant-ai/ha-mcp` 7.13.0 intentionally removed
that tool-wide annotation. The tool remains read-only for dashboard list,
search, and configuration arguments, but `include_screenshot=true` can persist
theme or dark-mode rendering preferences and is therefore classified upstream
as a write.

The reviewed upstream source is:

```text
homeassistant-ai/ha-mcp
version: 7.13.0
commit: f4eb53621ccb814cb7123d2811e06eda3577129c
```

At that commit, the upstream argument classifier permits configuration,
inventory, and search calls when `include_screenshot` is absent or false and
classifies screenshot rendering as a write because the rendering client can
persist preferences. This assertion is not generalized to any other version.

Reviewed source:

- [`ha_config_get_dashboard`](https://github.com/homeassistant-ai/ha-mcp/blob/f4eb53621ccb814cb7123d2811e06eda3577129c/src/ha_mcp/tools/tools_config_dashboards.py#L1258)
- [dashboard read/write argument classifier](https://github.com/homeassistant-ai/ha-mcp/blob/f4eb53621ccb814cb7123d2811e06eda3577129c/src/ha_mcp/read_only.py#L144-L149)
- [mixed-tool exemption registration](https://github.com/homeassistant-ai/ha-mcp/blob/f4eb53621ccb814cb7123d2811e06eda3577129c/src/ha_mcp/read_only.py#L191-L195)
- [dashboard hash algorithm](https://github.com/homeassistant-ai/ha-mcp/blob/f4eb53621ccb814cb7123d2811e06eda3577129c/src/ha_mcp/utils/config_hash.py#L11-L27)

## Decision

Support two explicit trust modes:

### `contract_read_only`

This remains preferred. The upstream tool must affirmatively declare
`readOnlyHint=true`, expose the required argument semantics, and not declare
destructive behavior.

### `reviewed_argument_constrained`

This exception is limited to profile:

```text
ha_mcp_7_13_dashboard_read_v1
```

It requires exact agreement on:

- initialize server name `ha-mcp`;
- server version `7.13.0`;
- MCP protocol `2025-03-26`;
- tool name `ha_config_get_dashboard`;
- the complete reviewed tool contract fingerprint;
- the exact reviewed annotation object;
- Engineering-owned exact invocation builders.

The reviewed contract fingerprint is SHA-256 over the complete JSON-compatible
tool object returned by `tools/list`, serialized with sorted keys, compact
separators, `ensure_ascii=false`, UTF-8 encoding, and no implicit value
coercion. It is not the catalog fingerprint and is unaffected by unrelated tool
additions.

Allowed calls are only:

```json
{"list_only": true, "include_screenshot": false}
```

and:

```json
{
  "url_path": "<validated-canonical-path>",
  "list_only": false,
  "force_reload": true,
  "include_screenshot": false
}
```

`force_reload=false` is the only permitted variation.

The public tools do not accept raw MCP arguments. The transport revalidates the
exact shape immediately before `tools/call`. Screenshots, view selection,
rendering preferences, dimensions, unknown arguments, and other tool names
fail before network dispatch.

## Consequences

- The provider does not claim the upstream tool is read-only.
- Health reports the selected trust mode and profile.
- Any upstream identity, version, protocol, annotation, input/output contract,
  or full reviewed contract drift makes the capability unavailable.
- The complete upstream catalog remains observability evidence, not a security
  pin.
- The dual dashboard-hash contract remains unchanged.
- Generic Standard HA MCP delegation remains unavailable.
- No dashboard write or preference-persistence path is added.

This is a consciously accepted risk for one operator and one reviewed upstream
release. It is not a general delegation model and must not become a version
range, implementation-family guess, or arbitrary argument forwarder.

## Alternatives rejected

- Treating `destructiveHint=false` as equivalent to read-only: non-destructive
  operations may still mutate state.
- Accepting any missing `readOnlyHint`: this would trust unrelated endpoints.
- Pinning only the input-schema fingerprint: annotations and other dispatch
  metadata could drift independently.
- Pinning the whole catalog: unrelated upstream tool additions would
  unnecessarily disable the reviewed dashboard operation.
- Enabling screenshot mode: preference persistence is outside RC3A.
- Enabling `StandardHaMcpGateway`: it remains an unavailable generic boundary.

## Future replacement

A dedicated upstream pure-read dashboard tool with `readOnlyHint=true` should
replace this exception. Any later upstream version requires a new review,
fixture, profile, tests, and explicit decision.
