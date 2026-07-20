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

The reviewed fixture originally hashed to `170c2aac...`, while the exact
published runtime hashed to `dd12cba0...`. Reproduction with the public
`ha-mcp-addon-amd64:7.13.0` OCI index
`sha256:f6c0d3379b625687757f55be51e786ecbc46ab7ad96c994208aec9dc2344396a`
proved the only field addition was `_meta.ha_mcp` with conversation-agent
exposure and pinning values. Upstream describes this middleware stamp as
additive metadata for its Home Assistant LLM API; it does not alter a regular
MCP client's tool call. The complete input schema and safety annotations were
unchanged.

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
ha_mcp_7_13_dashboard_read_v1 (historical, deprecated as the active runtime
profile by `ha_mcp_dashboard_read_v2` exact-release attestations)
```

It requires exact agreement on:

- initialize server name `ha-mcp`;
- server version `7.13.0`;
- MCP protocol `2025-03-26`;
- tool name `ha_config_get_dashboard`;
- the complete input-schema fingerprint;
- the reviewed security-contract projection fingerprint;
- the exact reviewed annotation object;
- Engineering-owned exact invocation builders.

Three fingerprints are maintained. All use SHA-256 over JSON-compatible values
serialized with sorted keys, compact separators, `ensure_ascii=false`, UTF-8,
and no implicit value coercion.

1. The complete input-schema fingerprint is an exact blocking gate.
2. The reviewed security-contract fingerprint is an exact blocking gate over
   tool name, complete input schema, output-schema presence/value, safety-hint
   presence/value, and all otherwise-unreviewed top-level or metadata fields.
3. The complete runtime-descriptor fingerprint is observability evidence. It
   may drift without blocking only when the security projection still matches.

The projection excludes only top-level display title/description,
`annotations.title`, `_meta.fastmcp.tags`, and
`_meta.ha_mcp.{llm_api_exposed,pinned}`. The first three are display,
documentation, and grouping data. The last two affect only upstream
conversation-agent exposure/pinning; Engineering does not select tools or
construct arguments from them. Unknown annotation keys, unknown metadata
fields, output-schema changes, and every input-schema field remain blocking.
This exclusion list is closed rather than pattern-based.

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
  or reviewed security-contract drift makes the capability unavailable.
- Complete-descriptor presentation drift remains visible with expected and
  observed fingerprints and a bounded `descriptive_metadata_only` status.
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
- Pinning the complete serialized descriptor: upstream presentation middleware
  adds `_meta` values that do not change Engineering dispatch safety and caused
  the dev2 live false negative.
- Pinning the whole catalog: unrelated upstream tool additions would
  unnecessarily disable the reviewed dashboard operation.
- Enabling screenshot mode: preference persistence is outside RC3A.
- Enabling `StandardHaMcpGateway`: it remains an unavailable generic boundary.

## Future replacement

A dedicated upstream pure-read dashboard tool with `readOnlyHint=true` should
replace this exception. Any later upstream version requires a new review,
fixture, profile, tests, and explicit decision.
