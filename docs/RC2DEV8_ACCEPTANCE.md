# RC2dev8 acceptance checklist

## Release integrity

1. Confirm the accepted PR merge is current `main`.
2. Confirm runtime, add-on, validator, and workflow-derived version are all
   `2.0.0-rc2-dev8`.
3. Confirm the protected promotion publishes only:
   - `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev8`
   - `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-<merge-sha>`
4. Confirm both tags resolve anonymously to one amd64/arm64/arm-v7 index with
   matching OCI revision, version, created time, `dirty=false`, SLSA provenance,
   and SPDX SBOM.
5. Confirm annotated tag `v2.0.0-rc2-dev8` targets the exact merge SHA and the
   promotion summary reports `release_complete=true`.
6. Do not modify RC2dev7 artifacts or stable v1.1.2.

## Runtime identity and health

1. Call `server_info`; verify version `2.0.0-rc2-dev8`, the promoted merge SHA,
   populated UTC build time, and `build_dirty=false`.
2. Call `list_capabilities`; verify 40 registered, 25 canonical, zero planned,
   schema version 1, and unchanged legacy enforcement metadata.
3. Call `get_server_health`; verify HA connected, audit and governance healthy,
   provider operational failures unchanged, and fallback/prohibited fallback
   both zero.
4. Perform one harmless dashboard 7.13.0 read and one harmless direct entity
   read.

## Raw enforcement acceptance

Use a raw stateless Streamable HTTP MCP client. Do not call a real service,
reload, deletion, or automation write.

1. Send `call_service` with `data_json` transmitted as the JSON string `"{}"`.
   Require HTTP 200, MCP `isError=false`, Engineering
   `error_code=provider_unavailable`, selected provider `standard_ha_mcp`, zero
   HA/upstream requests, and no fallback.
2. Send `upsert_automation` with a unique nonexistent ID and `config_json`
   transmitted as a JSON string containing deliberately invalid HA automation
   data. Require `provider_prohibited`, `replacement=create_change_plan`, zero
   dispatch/persistence, and confirm the probe remains absent.
3. Send harmless `reload_domain` and nonexistent `delete_automation` probes.
   Require their canonical `provider_unavailable` and `provider_prohibited`
   envelopes with zero dispatch.
4. Repeat one prohibited tool with malformed arguments. Policy must still win.
5. Send malformed arguments to a normal read tool. FastMCP validation must
   remain active and the tool/provider must not run.

## Audit reconciliation

For all four legacy calls require:

- top-level `event=tool_call`;
- matching tool and request IDs;
- `result_status=failure`;
- exact response `error_code` in audit;
- empty HA endpoint categories;
- no raw caller payload, Pydantic exception, secret, or access path;
- no provider, fallback, governance, service, reload, or automation-write
  evidence.

For the malformed normal read require `invalid_request`, failure status,
bounded argument field names only, and no provider dispatch.

## Final comparison and rollback

1. Call `server_info`, `list_capabilities`, and `get_server_health` again.
2. Confirm catalog, HA, dashboard, audit, governance, provider, dependency-index,
   and fallback states did not regress.
3. Roll back only Engineering Beta to
   `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev7` if a prohibited
   operation reaches HA/upstream, structured enforcement is absent, audit says
   success for an error, normal validation is bypassed, schemas change,
   governance changes unexpectedly, dashboard 7.13.0 trust regresses, or
   provenance does not match source.

ha-mcp 7.14.0 review and upgrade acceptance are explicitly deferred to
RC2dev9.
