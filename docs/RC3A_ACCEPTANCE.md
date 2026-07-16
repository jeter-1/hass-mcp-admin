# HA MCP Engineering Server RC3A staged acceptance

This procedure is a deployment handoff, not authorization to deploy from the
implementation pull request. Do not use it until the draft PR is reviewed,
accepted, merged, tagged, published, and anonymously pullable.

## Pre-deployment gate

Record:

- accepted commit SHA;
- tag `v2.0.0-rc.2.rc3a.1`;
- image
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2.rc3a.1`;
- immutable `sha-<accepted-commit>` image;
- version-image manifest digest;
- rollback image
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`.

Require the release workflow to pass its complete validation gate, provenance
checks, all three architecture builds, immutable-tag guard, publication, and
credential-free anonymous manifest/pull verification. Do not refresh Home
Assistant before the anonymous check passes.

Back up the Engineering governance store and record its current health. Keep
production `hass_mcp_admin` v1.1.2 installed and running on port 8099.

In the Engineering Beta add-on configuration, set
`upstream_dashboard_mcp_url` to the operator-provided internal streamable-HTTP
URL containing the upstream secret path. Enter it only in the password-style
option. Do not repeat it in notes, logs, commands, screenshots, chat, or
acceptance evidence.

Expected post-update catalog:

- 40 registered tools;
- 25 canonical tools;
- 15 beta-native tools;
- zero planned capabilities;
- schema version 1.

Expected new health section: `upstream_dashboard`. The generic
`standard_ha_mcp_delegation` field must remain `unavailable`.

## Publication and installation sequence

1. Merge the accepted RC3A PR.
2. Confirm `main` contains the accepted commit.
3. Create exact tag `v2.0.0-rc.2.rc3a.1` on that commit.
4. Wait for validation and image publication.
5. Confirm version and `sha-<commit>` tags share one digest.
6. Confirm the manifest contains `linux/amd64`, `linux/arm64`, and
   `linux/arm/v7`.
7. Confirm an unauthenticated manifest inspection and pull succeed.
8. Record the digest and provenance.
9. Back up the Engineering governance store.
10. Configure the secret upstream URL without copying it elsewhere.
11. Refresh the Home Assistant add-on repository.
12. Update only **HA MCP Engineering Server Beta/RC**.
13. Do not modify or stop production v1.1.2.
14. Reconnect the Engineering MCP connector.

## Initial verification

Call `server_info` first and require:

- version `2.0.0-rc.2.rc3a.1`;
- build SHA equal to the accepted tagged commit;
- populated UTC RFC3339 build time;
- Home Assistant connection healthy;
- 40 registered and 25 canonical tools.

Call `get_server_health(check_ha=true)` and require:

- governance storage healthy;
- audit enabled;
- redaction enabled;
- existing Home Assistant connectivity healthy;
- `standard_ha_mcp_delegation=unavailable`;
- `upstream_dashboard.configured=true`;
- `upstream_dashboard.credential_present=true`;
- `upstream_dashboard.reachable=true`;
- `upstream_dashboard.capability_status=available`;
- actual sanitized upstream name and version present;
- MCP protocol version present;
- required tool present;
- required schema compatible;
- schema and catalog fingerprints present;
- `writes_allowed=false`;
- no endpoint, host, port, path, query, credential, or raw schema present.

Record a health/counter baseline. Confirm existing automation plan, external
approval, apply, verification, rollback, audit, and direct-read workflows remain
available without exercising an unnecessary write.

## Dashboard inventory

Call:

```json
{
  "limit": 100
}
```

through `list_dashboards`. Require:

- `success=true`;
- provider/routing/classification `upstream_dashboard`;
- one bounded upstream operation;
- source timestamp and sanitized upstream identity;
- required schema fingerprint;
- deterministic URL-path order;
- only identification/review metadata;
- `truncated=false` with complete coverage, or explicit partial coverage when
  `truncated=true`;
- no full dashboard configuration;
- no write, service, physical action, or fallback.

Repeat with `limit=1` when at least two dashboards exist and require at most one
result plus truthful truncation.

## Exact configuration evidence

Choose one known non-critical storage-mode dashboard and call:

```json
{
  "url_path": "<exact-canonical-path>",
  "force_reload": true
}
```

through `get_dashboard_config`. Require:

- `success=true`;
- exact URL-path addressing, not title matching;
- provider/routing/classification `upstream_dashboard`;
- configuration returned only as untrusted data;
- stable config hash;
- source timestamp, schema fingerprint, and completeness;
- sanitized upstream warnings retained when present;
- no endpoint or credential material;
- no dashboard change.

Perform a second unchanged read. Require the same configuration hash. A changed
source timestamp is expected; an unchanged configuration must not produce a
different hash.

Use an invalid path such as one containing `/`, `?`, whitespace, or uppercase.
Require local validation failure with `upstream_dispatch_occurred=false` and no
provider request/failure increment.

If a configuration exceeds the response limit, require a valid structured
`response_too_large` response with `configuration_returned=false`, estimated
size, response limit, and hash when available. Do not accept syntactically
truncated JSON or complete coverage.

## Security and redaction review

Using the operator's local secret value only as a search term, verify it and all
recognizable endpoint components are absent from:

- Engineering add-on logs;
- `get_server_health`;
- tool success and failure responses;
- `get_audit_log`;
- exception/traceback output;
- startup summaries.

Inspect audit records for the two tools. Inventory records may contain only the
limit and provider. Exact-read records may contain the canonical dashboard path,
force-reload flag, and provider. They must not contain the upstream endpoint,
dashboard configuration, card content, attributes, credentials, or raw schema.

Confirm prohibited upstream names cannot be supplied by either public tool.
Confirm `call_service`, `reload_domain`, `upsert_automation`, dashboard writes,
backup, and physical actions did not dispatch through the provider.

## Observation period

Keep RC3A active while the operator uses normal Engineering MCP workflows.
Record evidence for:

- upstream and Engineering server restarts;
- clean reconnection after upstream restart;
- handshake/schema-fingerprint stability;
- catalog-fingerprint changes;
- dashboard hash stability;
- connection and tool-call latency;
- timeouts and categorized failures;
- redaction in normal and failure paths;
- governance, external approval, audit, and existing automation workflow
  regressions.

RC3B must not begin until RC3A is operationally stable and this evidence is
recorded.

## Rollback

If RC3A fails acceptance:

1. Stop dashboard-provider testing; do not attempt a dashboard write.
2. Capture only sanitized health, counters, timestamps, fingerprints, and error
   categories.
3. Reinstall or select immutable image
   `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`.
4. Remove the RC3A-only upstream option if the RC2 metadata editor requires it;
   never copy its value elsewhere.
5. Restart/update only the Engineering Beta add-on as required by the operator.
6. Reconnect only the Engineering connector.
7. Confirm RC2 identity, provenance, 38/25/0 catalog, governance health, audit,
   redaction, and existing automation workflows.
8. Leave production v1.1.2 unchanged.

Rollback removes the two RC3A dashboard tools. It does not mutate dashboard
configuration or the upstream Home Assistant MCP server.

## RC3B questions

Before RC3B, decide from recorded RC3A evidence:

- whether to pin a specific sanitized upstream identity;
- whether schema fingerprints require an explicit operator approval process;
- whether connection reuse is justified by measured latency and restart
  behavior;
- which exact dashboard-change-plan representation is safe;
- how immutable pre-change dashboard evidence and rollback artifacts will be
  stored without secrets;
- which external approval and executor boundaries must govern any future
  dashboard mutation.

RC3A itself answers none of those questions by adding a write path.
