# HA MCP Engineering Server 2.0.0-rc.2 deployed acceptance

Run this procedure only after the accepted RC2 PR is merged, exact tag
`v2.0.0-rc.2` points to the accepted main commit, publication succeeds, the
manifest contains amd64/arm64/arm-v7, and an unauthenticated pull succeeds.
Production `hass_mcp_admin` v1.1.2 must remain installed and running throughout.

## Publication and installation gate

1. Confirm the exact tag points to the intended accepted main commit.
2. Confirm the version image is
   `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`.
3. Confirm the commit image is tagged `sha-<tagged-commit>`.
4. Record the version-tag digest and confirm both tags resolve to it.
5. Confirm the manifest contains `linux/amd64`, `linux/arm64`, and
   `linux/arm/v7`.
6. Confirm a credential-free manifest inspection or pull succeeds.
7. Refresh the Home Assistant repository and confirm it offers RC1-to-RC2.
8. Update only **HA MCP Engineering Server Beta/RC** and reconnect only its MCP
   connector. Do not modify production v1.1.2.

## Identity first

Before any other acceptance call, invoke `server_info` and require:

- version `2.0.0-rc.2`;
- schema version 1;
- non-`unknown` `build_sha` equal to the tagged source commit;
- non-`unknown` UTC RFC3339 `build_time`.

Stop if any value is stale or unknown; that indicates Home Assistant is not
running the accepted RC2 image.

Call `list_capabilities` and require 38 registered tools, 25 canonical tools,
zero planned capabilities, and `search_entities` metadata of
`transitional_direct / direct_ha_api`.

Call `get_server_health`, capture a provider-counter baseline, and require:

- `search_entities` in `approved_direct_read_tools`;
- Standard HA MCP delegation remains unavailable;
- Standard HA MCP exact mapping count remains zero;
- explicit direct-policy enforcement remains enabled;
- fallback and prohibited-fallback attempts are zero.

## Required narrow search

Call:

```json
{"query":"garage","domain":"cover","limit":10}
```

Require:

- `success=true` and no `provider_unavailable`;
- provider `direct_ha_api`;
- classification `transitional_direct`;
- direct policy ID `bounded_entity_state_search`;
- exactly one Home Assistant request;
- results contain only `entity_id`, `state`, and `friendly_name`;
- completeness is `complete`, or explicitly `partial` when `truncated=true`;
- no service call, fallback, or write.

## Required bounded broad search

Call:

```json
{"query":"","domain":"sensor","limit":5}
```

Require no more than five results, deterministic `entity_id` order, explicit
truncation when more matches exist, partial completeness only when truncated,
one direct request, no provider failure, and no fallback.

## No-match and invalid-input checks

Use a unique nonexistent query. Require `count=0`, `results=[]`,
`truncated=false`, complete coverage, and exactly one successful direct read.

Then use one invalid domain such as `sensor.bad` or an invalid limit such as
`0` or `101`. Require a local `invalid_request` response before Home Assistant
access and no change to direct-provider request, success, or failure counters.

## Final reconciliation

Call `get_server_health` again and reconcile the exact delta:

- each valid search incremented direct request/success exactly once;
- the invalid request incremented no provider counter;
- a truncated success incremented partial results, not provider failures;
- Standard HA MCP requests remain zero;
- fallback and prohibited-fallback attempts remain zero;
- no timeout, service call, or write occurred.

Review the bounded audit records. They may record search intent and outcome but
must not contain full entity attributes, access secrets, Supervisor tokens, or
registry credentials. Continue the unchanged RC acceptance and soak checks from
[`RC1_ACCEPTANCE.md`](RC1_ACCEPTANCE.md) only after these RC2-specific gates pass.
