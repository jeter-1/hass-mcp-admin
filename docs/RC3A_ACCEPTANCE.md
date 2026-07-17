# HA MCP Engineering Server RC3A acceptance

This is a post-merge operator handoff. The feature PR does not tag, publish,
deploy, access Home Assistant, or call the live upstream dashboard tool.

## Automated promotion gate

After the accepted feature PR is merged, the operator does not create a tag.
The main-push promotion workflow must:

1. acquire the repository-wide release lock;
2. validate the accepted main commit;
3. read `.release/next-version`;
4. prove dev3 is newer than dev2 and below final RC3 with AwesomeVersion 25.8.0;
5. prove the dev3 Git tag and GHCR image are absent;
6. create a local release commit updating authoritative version metadata;
7. rerun compile, metadata, and complete repository tests on that local commit;
8. build `linux/amd64`, `linux/arm64`, and `linux/arm/v7`;
9. publish dev3 and `sha-<release-commit>`;
10. anonymously verify both tags, the shared digest, architectures, and OCI
    provenance;
11. verify main has not changed;
12. atomically push the release commit and annotated dev3 tag.

If any pre-promotion check fails, neither main nor a tag is pushed. If an image
was published before a later failure, treat it as immutable reconciliation
evidence and do not silently reuse it.

Record from the successful workflow summary:

- version `2.0.0-rc2-dev3`;
- annotated tag `v2.0.0-rc2-dev3`;
- release commit SHA;
- version and SHA image tags;
- immutable OCI digest;
- all three architectures;
- anonymous verification result.

## Operator deployment

1. Back up the Engineering governance store.
2. Refresh the Home Assistant add-on store if necessary.
3. Select **Update** for Engineering Beta only.
4. Do not modify production v1.1.2.
5. Reconnect the Engineering MCP connector.
6. Call `server_info` first.

Require:

- version `2.0.0-rc2-dev3`;
- build SHA matching the automated release commit;
- populated UTC RFC3339 build time;
- Home Assistant connection healthy;
- 40 registered tools;
- 25 canonical tools;
- zero planned capabilities.

## Provider health

Call `get_server_health(check_ha=true)` and require:

- `configured=true`;
- `credential_present=true`;
- `reachable=true`;
- `capability_status=available`;
- observed name `ha-mcp`;
- observed version `7.13.0`;
- MCP protocol `2025-03-26`;
- required tool present;
- required schema compatible;
- `input_schema_match=true`;
- `reviewed_security_contract_match=true`;
- `runtime_descriptor_match=false`;
- `published_runtime_descriptor_match=true`;
- `runtime_descriptor_drift=descriptive_metadata_only`;
- `trust_mode=reviewed_argument_constrained`;
- `trust_profile=ha_mcp_7_13_dashboard_read_v1`;
- `reviewed_contract_match=true`;
- `argument_constraints_active=true`;
- `screenshots_allowed=false`;
- `preference_writes_allowed=false`;
- `writes_allowed=false`;
- Standard HA MCP delegation still unavailable.

Record the expected and observed input-schema, security-contract,
runtime-descriptor, and catalog fingerprints. Do not record
the configured endpoint or any recognizable secret-bearing fragment.

## Dashboard read acceptance

Call `list_dashboards` with:

```json
{"limit": 100}
```

Require success, deterministic bounded metadata, no configuration bodies, and
complete or explicitly truncated coverage. Audit must show only the public
limit/provider intent.

Choose one non-critical storage-mode dashboard and call
`get_dashboard_config` twice:

```json
{
  "url_path": "<exact-canonical-path>",
  "force_reload": true
}
```

Require:

- both calls succeed;
- identical 16-character `config_hash`;
- identical 64-character `engineering_config_hash`;
- configuration treated as untrusted data;
- no screenshot or rendering metadata requested;
- no dashboard or preference state changed;
- no fallback or Standard MCP request.

Verify a locally invalid path fails before upstream dispatch. Confirm health and
audit counters reconcile and contain no endpoint, host, port, secret path,
query, credentials, raw schema, dashboard body, or card attributes.

## Rollback

If validation fails:

1. stop dashboard-provider acceptance;
2. retain only sanitized health, reason codes, fingerprints, and workflow
   evidence;
3. reinstall immutable image
   `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev2`;
4. reconnect only the Engineering connector;
5. confirm governance, external approval, audit, redaction, and existing
   automation workflows remain healthy;
6. leave production v1.1.2 unchanged.

Rollback restores dev2's fail-closed full-descriptor gate and does not modify
dashboards or the upstream server.

## Connector discovery

After reconnecting, require the client to expose all 40 server-side tools,
including `list_dashboards` and `get_dashboard_config`. Both dashboard tools
must advertise `readOnlyHint=true` and only their narrow public inputs. If raw
Engineering `tools/list` returns 40 while a ChatGPT connector still shows 38,
classify that discrepancy as connector schema caching and refresh, reconnect,
or recreate the connector before dashboard acceptance. Provider unavailability
does not remove either dashboard tool from server-side discovery.
