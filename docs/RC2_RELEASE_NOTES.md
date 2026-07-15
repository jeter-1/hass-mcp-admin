# HA MCP Engineering Server 2.0.0-rc.2 release notes

## Release identity

- Version: `2.0.0-rc.2`
- Release tag: `v2.0.0-rc.2`
- Generic image: `ghcr.io/jeter-1/hass-mcp-engineering-beta`
- Version image: `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`
- Source image: `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-<commit>`
- Platforms: `linux/amd64`, `linux/arm64`, and `linux/arm/v7`

RC2 supersedes RC1 because the reviewed `search_entities` provider-routing
correction was added after RC1 had already been built or deployed while the
add-on version and image tag remained `2.0.0-rc.1`. Home Assistant therefore
had no distinct update to install and could continue running the original RC1
runtime, which returned `provider_unavailable` for `search_entities`.

RC1 is immutable. Do not move or delete `v2.0.0-rc.1`, overwrite image tag
`2.0.0-rc.1`, force-push rewritten RC1 source, or publish an RC1 replacement.
RC2 gives the corrected source a new Home Assistant version and immutable image
tag without changing production v1.1.2.

## Compatibility boundary

RC2 preserves:

- add-on slug `hass_mcp_engineering_beta`;
- MCP port `8100` and Ingress-only administrator port `8110`;
- `ingress: true` and `panel_admin: true`;
- 38 registered tools, 25 canonical tools, and zero planned capabilities;
- schema version 1 and every Beta 26 public input schema and enum;
- external approval authority version 2 and Beta 26 expiry idempotency;
- every canonical provider route except the reviewed `search_entities`
  correction;
- production `hass_mcp_admin` v1.1.2 on port `8099`.

## Entity-search correction

`search_entities` maps to capability `broad_entity_search` and now has one
explicit route:

- classification: `transitional_direct`;
- provider: `direct_ha_api`;
- fallback: none;
- policy ID: `bounded_entity_state_search`.

Standard HA MCP remains unavailable. The direct policy authorizes exactly one
read-only `GET /states` inventory. Local validation accepts an empty or
canonical `[a-z0-9_]+` domain and an integer limit from 1 through 100 before any
Home Assistant request or provider accounting. Matching is case-insensitive
against `entity_id` and `friendly_name`, domain filtering is exact, results are
sorted by `entity_id`, and output contains only `entity_id`, `state`, and
`friendly_name`. `truncated=true` reports bounded partial coverage. There is no
service call, write, implicit fallback, registry join, or fuzzy search.

## Controlled image publication and provenance

Pull-request CI validates but never publishes the RC image. Publication begins
only when the accepted main commit is tagged exactly `v2.0.0-rc.2`. The workflow
runs the complete validation gate, then verifies the tag, `config.yaml` version,
and server version all equal `2.0.0-rc.2` before registry login or push.

The release run resolves the checked-out commit with `git rev-parse HEAD` and
generates one bounded UTC RFC3339 build time. One three-platform Buildx
invocation receives:

- `BUILD_VERSION=2.0.0-rc.2`;
- `HAMCP_BUILD_SHA=<exact checked-out commit>`;
- `HAMCP_BUILD_TIME=<one shared UTC RFC3339 value>`.

The version and full-SHA tags share one multi-architecture manifest. OCI source,
revision, created, and version labels carry the same values. Only the publish
job has `packages: write`; immutable-tag checks fail closed. A separate job with
no credentials must confirm all three platforms, matching digests, and an
anonymous pull before Home Assistant is refreshed.

If GitHub initially creates the package as private, an administrator must open
the `hass-mcp-engineering-beta` package settings, change visibility to Public,
and rerun anonymous verification. Do not refresh Home Assistant until the
credential-free check passes.

## Exact post-merge operator sequence

1. Merge the accepted RC2 PR.
2. Confirm `main` contains the expected accepted commit.
3. Create tag `v2.0.0-rc.2`.
4. Confirm the tag points to the intended main commit.
5. Wait for the RC image validation and publication workflow.
6. Confirm publication of `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc.2`.
7. Confirm the multi-architecture manifest includes amd64, arm64, and arm/v7.
8. Confirm unauthenticated manifest inspection or pull succeeds.
9. Record the version-tag digest.
10. Refresh the Home Assistant add-on repository.
11. Confirm Home Assistant offers an update from RC1 to RC2.
12. Update only HA MCP Engineering Server Beta/RC.
13. Do not disable or modify production v1.1.2.
14. Reconnect the Engineering MCP connector.
15. Call `server_info` first.
16. Confirm version `2.0.0-rc.2`, `build_sha` matches the tagged commit, and
    `build_time` is populated UTC RFC3339.
17. Call `list_capabilities`.
18. Confirm 38 registered, 25 canonical, zero planned, and `search_entities` is
    `transitional_direct / direct_ha_api`.
19. Call `get_server_health`.
20. Confirm `search_entities` is approved for direct read, Standard HA MCP
    remains unavailable, and fallback attempts are zero.

Continue with [`RC2_ACCEPTANCE.md`](RC2_ACCEPTANCE.md). Merging this PR does not
authorize creating the tag, publishing an image, refreshing Home Assistant, or
accessing a deployed environment.
