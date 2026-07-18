# RC2dev7 acceptance

RC2dev7 acceptance is read-only until the isolated transport sequence is
explicitly started. Do not modify stable v1.1.2.

## Promotion gate

1. Merge the accepted draft PR.
2. Confirm `main` contains the expected merge commit.
3. Wait for the protected main-branch promotion workflow.
4. Confirm it builds the exact merge SHA and publishes:
   - `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev7`
   - `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-<merge-sha>`
5. Confirm both tags share one anonymous multi-architecture digest with
   amd64, arm64, and arm/v7 plus SLSA provenance and SBOM attestations.
6. Confirm annotated tag `v2.0.0-rc2-dev7` targets the release SHA and the
   workflow reports `release_complete=true`.
7. Only then refresh and update Engineering Beta.

## Focused smoke test

1. Call `server_info`; verify version `2.0.0-rc2-dev7`, the accepted merge SHA,
   UTC build time, and `build_dirty=false`.
2. Call `list_capabilities`; verify 40 registered, 25 canonical, zero planned,
   and schema version 1.
3. Call `get_server_health`; verify Home Assistant, audit, governance, provider,
   and fallback health remain unchanged.
4. Call `list_dashboards` and one harmless `get_entity` read.
5. Read `get_audit_log` without a filter.
6. Query `auth_failure`, `auth_failure_throttled`, and `rate_limited` before
   sending new invalid requests.
7. Confirm each response contains only records whose top-level event exactly
   matches the requested value.
8. Confirm the filter queries themselves appear under `event="tool_call"` and
   never contaminate another filter.

## Resume isolated authentication acceptance

After the smoke test passes:

1. Establish a dedicated caller identity and capture health/audit baselines.
2. Send invalid requests 1–5; verify HTTP 404, `authentication_failure`, and
   exactly five top-level `auth_failure` events.
3. Query `auth_failure_throttled`; verify zero matches and no self-audited false
   positive.
4. Send request 6; verify HTTP 429, `rate_limit_exceeded`, exactly one top-level
   `auth_failure_throttled`, and no duplicate ordinary failure.
5. Query all four supported filters and reconcile request IDs exactly.
6. Wait for authentication limiter refill and verify valid authentication.
7. Run the isolated authenticated general rate-limit test and verify only
   `rate_limited` classification.
8. Complete the secret scan and verify no rejected request reached Home
   Assistant, upstream dashboard, any provider, fallback, service, or write.
9. Recheck final health and confirm stable v1.1.2 was not accessed.

## Rollback

Rollback only Engineering Beta to:

`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev6`

Rollback if filtering self-contaminates, nested values match, valid top-level
records disappear, malformed content leaks, redaction fails, authentication
changes, public schemas drift, provider/governance health regresses, or image
provenance does not match the merge SHA.
