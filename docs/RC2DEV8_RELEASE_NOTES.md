# RC2dev8 release notes

Version: `2.0.0-rc2-dev8`

Baseline: `2b4a5ed11380fa69b91cbcc0ad1375825e436597` (the verified PR #42
merge and deployed RC2dev7 source revision).

## Why this release exists

RC2dev7 remained fail closed during raw MCP acceptance: no service,
automation, provider, Home Assistant, upstream, fallback, governance, or
persistence operation ran. However, FastMCP/Pydantic converted the public JSON
string fields `data_json` and `config_json` into objects before handler
validation. `call_service` and `upsert_automation` therefore returned
`isError=true` validation text instead of their documented Engineering policy
envelopes. Because HTTP remained 200 and request telemetry had no error code,
their audit records incorrectly said `result_status=success` and
`error_code=null`.

## Correction

The authenticated Streamable HTTP gateway now reads only the requested tool
name and intercepts this closed allowlist before FastMCP argument processing:

| Tool | Canonical outcome | Provider | Fallback |
|---|---|---|---|
| `call_service` | `provider_unavailable` | selected `standard_ha_mcp`, not dispatched | none |
| `reload_domain` | `provider_unavailable` | selected `standard_ha_mcp`, not dispatched | none |
| `upsert_automation` | `provider_prohibited` with `replacement=create_change_plan` | policy | none |
| `delete_automation` | `provider_prohibited` | policy | none |

The policy result is argument independent. Valid, missing, null, array, object,
extra, and otherwise malformed argument shapes receive the same canonical
result. The caller payload is not forwarded or echoed. Requests above the
existing two-megabyte transport bound are rejected first as `invalid_request`.

All other tools remain on the normal FastMCP path with unchanged public input
schemas and Pydantic validation.

## Audit semantics

The gateway now reconciles bounded JSON/SSE MCP output before writing the tool
audit record:

- a successful tool result remains `success`;
- a structured Engineering `success=false` response uses its exact stable
  `error_code` and records `failure`;
- an MCP `isError=true` validation or unknown-tool result records
  `invalid_request`/`failure`;
- an unclassified tool execution error records
  `internal_server_error`/`failure`;
- JSON-RPC parse, request, method, and parameter errors record
  `invalid_request`/`failure`.

Rejected validation values and raw Pydantic text are not audited. Only bounded
argument field names may be retained.

## Compatibility and safety

- 40 registered tools, 25 canonical tools, zero planned tools, schema version
  1.
- All public Beta input schemas are unchanged, including `data_json: string`
  and `config_json: string`.
- Governance storage and approval behavior are unchanged.
- Stable v1.1.2 source, packaging, slug, ports, image, and storage are unchanged.
- The reviewed dashboard profile remains exactly ha-mcp 7.13.0; 7.14.0 is not
  accepted or reviewed here.
- RC2dev7 tags and images remain immutable.

## Deferred to RC2dev9

ha-mcp 7.14.0 requires a separate source/artifact review, exact trust profile,
and rolling-upgrade acceptance. No version range or new upstream tool was added
to RC2dev8.

## Remaining environment bake gaps

These existing gaps are not represented as completed by this source release:

- exact-image refresh-failure preservation;
- beyond-hard-TTL refusal and recovery;
- direct running-container `RepoDigest` proof.
