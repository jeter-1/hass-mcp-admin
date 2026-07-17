# HA MCP Engineering Server RC3A release notes

## Release state

- Advertised version before successful promotion: `2.0.0-rc2-dev2`
- Automated promotion target: `2.0.0-rc2-dev3`
- Automated tag: `v2.0.0-rc2-dev3`
- Promoted image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev3`
- Rollback image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev2`

The feature PR intentionally does not change Home Assistant add-on metadata to
dev3. After merge, the promotion workflow consumes
`.release/next-version`, creates a local release commit, publishes and
anonymously verifies the multi-architecture image, and only then atomically
pushes the release commit and annotated tag. A failed publication leaves the
advertised add-on version at dev2.

AwesomeVersion 25.8.0 orders dev3 newer than dev2 and below final
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
- the complete input schema and reviewed security-contract projection;
- the exact reviewed annotations;
- exact Engineering-owned invocation builders.

The expected input-schema fingerprint is:

```text
7f2b6a086faec129c182fe6f791722beda9fffc659a507f55a3b20d72e2155a6
```

The reviewed security-contract fingerprint is:

```text
c4395cfa63e9de34a672cfdfe34f93541b407766c81b9dcbe82bf4f82c3e7b86
```

The reviewed fixture's complete runtime-descriptor fingerprint remains:

```text
170c2aac1d6437d5c42b7f1d48f5322fef4736c414654c4cc4f7830138e959ca
```

The published 7.13.0 runtime descriptor is:

```text
dd12cba02e59bf98e5b251ddf516c5a7fbea5fbd5f37d053cd8a9cc549827157
```

The exact published artifact inspected was
`ghcr.io/homeassistant-ai/ha-mcp-addon-amd64:7.13.0` at immutable OCI index
digest
`sha256:f6c0d3379b625687757f55be51e786ecbc46ab7ad96c994208aec9dc2344396a`.
It runs Python 3.13.12 with FastMCP 3.4.4, MCP 1.24.0, and Pydantic 2.13.4.
The installed dashboard source is byte-identical to reviewed commit
`f4eb53621ccb814cb7123d2811e06eda3577129c`. The image publishes Home
Assistant labels but no OCI source or revision label; the release workflow's
exact tag checkout and the byte-identical installed source establish the
reviewed revision.

Reproducing `tools/list` with that exact dependency set yields the live
`dd12...` fingerprint. The recursive descriptor diff is limited to addition of
`_meta.ha_mcp.llm_api_exposed=true` and `_meta.ha_mcp.pinned=false` by upstream
`LlmExposureMiddleware`. Raw top-level/property ordering also differs, but
sorted canonical serialization removes that difference. Tool name, title,
description, input schema, annotations, output-schema omission, defaults,
nullable representation, and FastMCP tags are identical.

RC3A now separates three hashes, each using SHA-256 over compact, sorted,
UTF-8 JSON with `ensure_ascii=false`, no `default=str`, and JSON-compatible
values only:

- `input_schema_fingerprint` covers the complete input schema and remains an
  exact blocking gate;
- `reviewed_security_contract_fingerprint` covers tool name, the complete
  input schema, output-schema presence/value, safety-annotation
  presence/value, and every unreviewed top-level or metadata field;
- `runtime_descriptor_fingerprint` covers the complete descriptor and is
  observability evidence.

Only top-level display title/description, `annotations.title`, FastMCP grouping
tags, and the two upstream conversation-agent exposure/pinning values are
excluded from the security projection. They neither select the tool, change
its accepted arguments, alter its output/hash contract, nor influence the
Engineering-owned invocation builder. Every other unexpected field remains in
the blocking projection. Dictionary order never affects any fingerprint.

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
  "input_schema_match": true,
  "reviewed_security_contract_match": true,
  "runtime_descriptor_match": false,
  "published_runtime_descriptor_match": true,
  "runtime_descriptor_drift": "descriptive_metadata_only",
  "reviewed_contract_match": true,
  "argument_constraints_active": true,
  "screenshots_allowed": false,
  "preference_writes_allowed": false,
  "writes_allowed": false
}
```

Identity, version, protocol, input-schema, security-contract, annotation,
output-contract, argument, and hash drift fail closed under stable bounded
categories. Complete-descriptor drift is non-blocking only when the security
projection still matches, and is then reported as descriptive metadata. Health never exposes the
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
